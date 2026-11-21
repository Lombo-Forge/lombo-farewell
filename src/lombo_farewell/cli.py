"""Command line: doctor, plan, run, keep. Talks to the outside world; core.py decides.

lombo-farewell doctor        check the settings, Jellyfin and the source; exit 1 on any failure
lombo-farewell plan          show what would happen; changes nothing, ever
lombo-farewell run [--loop]  mirror the list into the collection (only when DRY_RUN=false)
lombo-farewell keep TITLE    postpone one title's deletion by KEEP_DAYS (only when DRY_RUN=false)

It never deletes a title or a file. If the source cannot be read the collection is left as it is.
"""

import argparse
import os
import sys
import time
from collections.abc import Callable

from . import __version__, core, engine, notify, state
from .config import PREFIX, SETTINGS, Config, ConfigError, from_env
from .engine import Source
from .jellyfin import Jellyfin, JellyfinError
from .sources import SourceError

Failure = (JellyfinError, SourceError, notify.NotifyError, state.StateError)


def _day(ts: float | None) -> str:
    if ts is None:
        return "unknown"
    try:
        return time.strftime("%Y-%m-%d", time.gmtime(ts))
    except (OverflowError, OSError, ValueError):  # NaN, infinity or a number too large for a date
        return "unknown"


def doctor(cfg: Config) -> int:
    jf = Jellyfin(cfg.jellyfin_url, cfg.jellyfin_api_key)
    source = engine.make_source(cfg)
    ok = True

    def reachable() -> str:
        info = jf.public_info()
        return f"{info.get('ServerName')} {info.get('Version')}"

    def key_accepted() -> str:
        jf.info()
        return "yes"

    def readable() -> str:
        return f"{cfg.source}: {len(source.fetch())} title(s) scheduled"

    def collection() -> str:
        found = jf.find_collection(cfg.collection_name)
        return f"{cfg.collection_name!r} " + (
            "exists" if found else "does not exist yet (it is made on the first live run)"
        )

    checks: list[tuple[str, Callable[[], str]]] = [
        ("Jellyfin reachable", reachable),
        ("API key accepted", key_accepted),
        ("source readable", readable),
        ("collection", collection),
    ]
    for label, check in checks:
        try:
            print(f"[ ok ] {label}: {check()}")
        except (JellyfinError, SourceError) as e:
            print(f"[FAIL] {label}: {e}")
            ok = False
    keep_note = "" if source.can_postpone else " (the file source cannot postpone, so `keep` is refused)"
    print(f"[ ok ] keep it longer: {cfg.keep_days} days{keep_note}")
    print(f"[ ok ] webhook: {'set' if cfg.webhook_url else 'not set (alerts are skipped)'}")
    print(f"[ ok ] mode: {'dry run (nothing is changed)' if cfg.dry_run else 'LIVE'}")
    return 0 if ok else 1


def show(
    entries: list[core.Entry],
    problems: list[str],
    report: engine.SyncReport,
    names: dict[str, str],
    collection_name: str = "",
) -> None:
    # Titles come from library metadata or a list file: strip control characters before they reach a terminal.
    for e in entries:
        print(f"leaving: {core.clean(str(e))}")
    for p in problems:
        print(f"skipped: {core.clean(p)}")
    if report.foreign:
        print(
            f"[WARN] the collection {core.clean(collection_name)!r} exists but was not made by this tool, so nothing "
            "in it is changed. Pick another LOMBO_FAREWELL_COLLECTION_NAME, or set LOMBO_FAREWELL_ADOPT=true to let "
            "this tool manage it (it removes titles that are not leaving)."
        )
    if report.shrink_blocked:
        print(
            f"[WARN] the leaving list shrank to {len(entries)} title(s) while the collection holds more than "
            "twice that, so nothing is removed this round. If the source really changed, set "
            "LOMBO_FAREWELL_ALLOW_SHRINK=true; otherwise check the source."
        )
    if report.created:
        print(f"collection: would create it with {len(report.added)} title(s)")
    for label, ids in (("add", report.added), ("remove", report.removed), ("leave in place for now", report.held_back)):
        for i in ids:
            if not (report.created and label == "add"):
                print(f"collection: {label} {core.clean(names.get(i, i))}")
    if not entries and not report.removed:
        print("Nothing is scheduled for deletion.")


def _names(entries: list[core.Entry], jf: Jellyfin, report: engine.SyncReport) -> dict[str, str]:
    names = {e.item_id: e.title for e in entries}
    unknown = [i for i in report.removed + report.held_back if i not in names]
    return {**jf.names(unknown), **names} if unknown else names


def plan(cfg: Config) -> int:
    """Read everything, change nothing."""
    jf = Jellyfin(cfg.jellyfin_url, cfg.jellyfin_api_key)
    entries, problems, unresolved = engine.build_entries_full(engine.make_source(cfg), jf, time.time())
    owned = state.load(cfg.state_file)["owned"]
    report = engine.sync(jf, cfg, entries, apply=False, keep_ids=unresolved, owned_id=owned, check_owner=True)
    show(entries, problems, report, _names(entries, jf, report), cfg.collection_name)
    return 0


def run_once(cfg: Config, now: float | None = None) -> int:
    with state.locked(cfg.state_file):  # a manual `run` or `keep` and the hourly loop never overlap
        return _run_locked(cfg, time.time() if now is None else now)


def _run_locked(cfg: Config, now: float) -> int:
    jf = Jellyfin(cfg.jellyfin_url, cfg.jellyfin_api_key)
    entries, problems, unresolved = engine.build_entries_full(engine.make_source(cfg), jf, now)
    live = not cfg.dry_run
    saved = state.load(cfg.state_file)
    report = engine.sync(jf, cfg, entries, apply=live, keep_ids=unresolved, owned_id=saved["owned"], check_owner=True)
    show(entries, problems, report, _names(entries, jf, report), cfg.collection_name)
    if not live:
        print("Dry run: nothing was changed. Set LOMBO_FAREWELL_DRY_RUN=false to make the collection.")
        return 0
    if report.collection and not report.foreign:
        saved["owned"] = report.collection  # made by this tool, or adopted on purpose
    live_ids = {e.item_id for e in entries}
    saved["kept"] = {k: v for k, v in saved["kept"].items() if k in live_ids}
    late, remembered = core.overdue_to_alert(entries, set(saved["alerted"]))
    for e in late:
        text = (
            f"{core.clean(e.title)} is {e.overdue_days} days past its deletion date and still listed. "
            "Check the cleanup tool."
        )
        try:
            if cfg.webhook_url:
                notify.send(cfg.webhook_url, text)
            print(f"overdue: {core.clean(str(e))}")
        except notify.NotifyError as err:
            print(f"[FAIL] {err}", file=sys.stderr)
            remembered.discard(e.item_id)  # try again next round
    saved["alerted"] = sorted(remembered)
    state.save(cfg.state_file, saved)
    return 0


def keep(cfg: Config, query: str, now: float | None = None) -> int:
    with state.locked(cfg.state_file):
        return _keep_locked(cfg, query, time.time() if now is None else now)


def _keep_locked(cfg: Config, query: str, now: float) -> int:
    jf = Jellyfin(cfg.jellyfin_url, cfg.jellyfin_api_key)
    source: Source = engine.make_source(cfg)
    if not source.can_postpone:
        print(f"[FAIL] SOURCE={cfg.source} cannot postpone a deletion. Edit the list instead.", file=sys.stderr)
        return 1
    entries, _ = engine.build_entries(source, jf, now)
    entry, why = core.find(entries, query)
    if entry is None:
        print(f"[FAIL] {why}", file=sys.stderr)
        return 1
    saved = state.load(cfg.state_file)
    if core.keep_check(entry, saved["kept"].get(entry.item_id), now, cfg.keep_days) != "ok":
        print(
            f"[FAIL] {core.clean(entry.title)} was already kept; it can be kept again when its extra time is "
            f"nearly used up, and at most once every {cfg.keep_days} days.",
            file=sys.stderr,
        )
        return 1
    if entry.group is None or entry.media_id is None:  # pragma: no cover - Maintainerr entries always carry both
        print(f"[FAIL] {entry.title} has no postpone reference.", file=sys.stderr)
        return 1
    if cfg.dry_run:
        print(f"Dry run: would keep {core.clean(entry.title)} for {cfg.keep_days} more days. Nothing was changed.")
        return 0
    until = source.postpone(entry.group, entry.media_id, cfg.keep_days)
    saved["kept"][entry.item_id] = {"title": entry.title, "at": now, "until": until}
    state.save(cfg.state_file, saved)
    print(f"Kept {core.clean(entry.title)}: it now leaves on {_day(until)}.")
    return 0


def main(argv: list[str] | None = None, env: dict[str, str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="lombo-farewell", description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("doctor")
    sub.add_parser("plan")
    run = sub.add_parser("run")
    run.add_argument("--loop", action="store_true")
    kp = sub.add_parser("keep")
    kp.add_argument("title", help="the title (or its Jellyfin id) to keep longer")
    args = p.parse_args(argv)

    try:
        cfg = from_env(dict(os.environ) if env is None else env)
    except ConfigError as e:
        print(f"[FAIL] settings: {e}", file=sys.stderr)
        return 2

    for name in sorted(env if env is not None else os.environ):
        if name.startswith(PREFIX) and name[len(PREFIX) :] not in SETTINGS:
            print(f"[warn] {name} is not a setting of this tool and is ignored (typo?)", file=sys.stderr)

    try:
        if args.cmd == "doctor":
            return doctor(cfg)
        if args.cmd == "plan":
            return plan(cfg)
        if args.cmd == "keep":
            return keep(cfg, args.title)
        while True:
            try:
                run_once(cfg)
            except Failure as e:
                print(f"[FAIL] {e}", file=sys.stderr)  # the collection stays as it was; a loop survives one bad round
                if not args.loop:
                    return 1
            except Exception as e:  # noqa: BLE001  # one odd reply must not end the loop (and restart the container)
                print(f"[FAIL] unexpected error: {type(e).__name__}: {e}", file=sys.stderr)
                if not args.loop:
                    return 1
            if not args.loop:
                return 0
            time.sleep(cfg.interval_seconds)
    except Failure as e:
        print(f"[FAIL] {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
