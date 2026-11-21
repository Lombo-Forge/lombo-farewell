"""Command line: doctor, plan, run, keep. Talks to the outside world; core.py decides.

lombo-farewell doctor        check the settings, Jellyfin and the source; exit 1 on any failure
lombo-farewell plan          show what would happen; changes nothing, ever
lombo-farewell run [--loop]  mirror the list into the collection (only when DRY_RUN=false)
lombo-farewell keep TITLE    postpone one title's deletion by KEEP_DAYS (only when DRY_RUN=false)
lombo-farewell serve         small HTTP API: GET /v1/leaving, POST /v1/keep, GET /v1/health (needs API_TOKEN)

It never deletes a title or a file. If the source cannot be read the collection is left as it is.
"""

import argparse
import os
import sys
import time
from collections.abc import Callable

from . import __version__, api, core, engine, notify, state
from .config import PREFIX, SETTINGS, Config, ConfigError, from_env, is_loopback, parse_bind, token_problem
from .engine import Source
from .jellyfin import Jellyfin, JellyfinError
from .sources import SourceError

NON_LOOPBACK = (
    "LOMBO_FAREWELL_API_BIND is not a loopback address, so other machines can reach the API. Keep it on a private "
    "network and put your reverse proxy in front (examples/); never publish the port."
)

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
    problem = token_problem(cfg.api_token)
    if cfg.api_token and problem:
        print(f"[FAIL] API token: {problem}")
        ok = False
    elif problem:
        print("[ ok ] API: off (no LOMBO_FAREWELL_API_TOKEN, so `serve` will not start)")
    else:
        print(f"[ ok ] API: `serve` would listen on {cfg.api_bind}")
    if problem is None and not is_loopback(parse_bind(cfg.api_bind)[0]):
        print(f"[warn] API bind: {NON_LOOPBACK}")
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
    try:  # a view for other apps (docs/contracts.md): a failure here never fails the round
        rows = core.leaving_rows(entries, saved["kept"], now, cfg.keep_days)
        state.save_leaving(cfg.state_file, rows, now)
    except state.StateError as err:
        print(f"[warn] {err}", file=sys.stderr)
    return 0


def keep(cfg: Config, query: str, now: float | None = None) -> int:
    with state.locked(cfg.state_file):
        return _keep_locked(cfg, query, time.time() if now is None else now)


def _keep_locked(cfg: Config, query: str, now: float) -> int:
    done, message = keep_title(cfg, query, now)
    print(message if done else f"[FAIL] {message}", file=sys.stdout if done else sys.stderr)
    return 0 if done else 1


def keep_title(cfg: Config, query: str, now: float) -> tuple[bool, str]:
    """The one keep rule, shared by `keep` and the API: (done, message). Caller holds the run lock.

    A refusal is (False, why). A source, Jellyfin or state-file failure raises, as it always did.
    """
    jf = Jellyfin(cfg.jellyfin_url, cfg.jellyfin_api_key)
    source: Source = engine.make_source(cfg)
    if not source.can_postpone:
        return False, f"SOURCE={cfg.source} cannot postpone a deletion. Edit the list instead."
    entries, _ = engine.build_entries(source, jf, now)
    entry, why = core.find(entries, query)
    if entry is None:
        return False, why
    saved = state.load(cfg.state_file)
    if core.keep_check(entry, saved["kept"].get(entry.item_id), now, cfg.keep_days) != "ok":
        return False, (
            f"{core.clean(entry.title)} was already kept; it can be kept again when its extra time is "
            f"nearly used up, and at most once every {cfg.keep_days} days."
        )
    if entry.group is None or entry.media_id is None:  # pragma: no cover - Maintainerr entries always carry both
        return False, f"{core.clean(entry.title)} has no postpone reference."
    if cfg.dry_run:
        return (
            True,
            f"Dry run: would keep {core.clean(entry.title)} for {cfg.keep_days} more days. Nothing was changed.",
        )
    until = source.postpone(entry.group, entry.media_id, cfg.keep_days)
    saved["kept"][entry.item_id] = {"title": entry.title, "at": now, "until": until}
    state.save(cfg.state_file, saved)
    return True, f"Kept {core.clean(entry.title)}: it now leaves on {_day(until)}."


def leaving_now(cfg: Config, now: float) -> dict[str, object]:
    """The leaving list as it is right now (what the API returns). Reads only; raises on a failed read."""
    entries, _ = engine.build_entries(engine.make_source(cfg), Jellyfin(cfg.jellyfin_url, cfg.jellyfin_api_key), now)
    rows = core.leaving_rows(entries, state.load(cfg.state_file)["kept"], now, cfg.keep_days)
    return state.leaving_document(rows, now)


def serve(cfg: Config) -> int:
    """Run the HTTP API until stopped. Refuses to start without a good token."""
    problem = token_problem(cfg.api_token)
    if problem:
        print(f"[FAIL] serve: {problem}", file=sys.stderr)
        return 2
    host = parse_bind(cfg.api_bind)[0]

    def keep_request(title: str) -> tuple[bool, str]:
        with state.locked(cfg.state_file):
            return keep_title(cfg, title, time.time())

    server = api.make_server(cfg, keep_request, lambda: leaving_now(cfg, time.time()))
    if not is_loopback(host):
        print(f"[WARN] {NON_LOOPBACK}", file=sys.stderr)
    mode = "dry run: a keep changes nothing" if cfg.dry_run else "LIVE: a keep postpones a deletion"
    print(f"lombo-farewell API on {cfg.api_bind} ({mode})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def main(argv: list[str] | None = None, env: dict[str, str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="lombo-farewell", description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("doctor")
    sub.add_parser("plan")
    sub.add_parser("serve")
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
        if args.cmd == "serve":
            return serve(cfg)
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
