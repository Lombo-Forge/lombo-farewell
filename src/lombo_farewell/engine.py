"""The doing: gather entries from the source and Jellyfin, change the collection, postpone a title.

No printing, no clock (time is passed in); cli.py prints and reads the clock. The rules are in core.py.
"""

from dataclasses import dataclass, field
from typing import Protocol

from . import core
from .config import Config
from .core import Candidate, Entry
from .jellyfin import Jellyfin
from .sources import FileSource, MaintainerrSource


class Source(Protocol):
    can_postpone: bool

    def fetch(self) -> list[Candidate]: ...

    def postpone(self, group: str, media_id: str, days: int) -> float: ...


def make_source(cfg: Config) -> Source:
    if cfg.source == "file":
        return FileSource(cfg.source_file)
    return MaintainerrSource(cfg.maintainerr_url)


def build_entries(source: Source, jf: Jellyfin, now: float) -> tuple[list[Entry], list[str]]:
    """Entries for every title the source lists that Jellyfin knows, plus a note for each one it skipped.

    Raises SourceError or JellyfinError when something cannot be read; the caller then changes nothing.
    """
    entries, problems, _unresolved = build_entries_full(source, jf, now)
    return entries, problems


def build_entries_full(source: Source, jf: Jellyfin, now: float) -> tuple[list[Entry], list[str], frozenset[str]]:
    """Like build_entries, plus the ids the source lists but Jellyfin could not match this round.

    Those titles are still leaving, so the caller must not unlist them from the collection because of it.
    """
    cands = source.fetch()
    names = jf.names([core.norm_id(c.item_id) for c in cands if c.item_id])
    resolved: list[tuple[Candidate, str, str]] = []
    problems: list[str] = []
    unresolved: set[str] = set()
    for cand in cands:
        if cand.item_id:
            item = core.norm_id(cand.item_id)
            if item in names:
                resolved.append((cand, item, names[item]))
            else:
                unresolved.add(item)
                problems.append(f"{cand.title or cand.item_id}: Jellyfin does not know this id")
            continue
        title = cand.title or ""
        found = jf.ids_named(title)
        if len(found) == 1:
            resolved.append((cand, found[0], title))
        else:
            unresolved.update(core.norm_id(i) for i in found)
            problems.append(
                f"{title}: {'no title' if not found else f'{len(found)} titles'} with exactly this name in Jellyfin"
            )
    return core.plan(resolved, now, source.can_postpone), problems, frozenset(unresolved)


@dataclass
class SyncReport:
    collection: str | None = None
    created: bool = False
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    held_back: list[str] = field(default_factory=list)
    shrink_blocked: bool = False  # removals were held back because the list shrank too much at once
    foreign: bool = False  # the collection exists but was not made by this tool: nothing was changed


def sync(
    jf: Jellyfin,
    cfg: Config,
    entries: list[Entry],
    apply: bool,
    keep_ids: frozenset[str] = frozenset(),
    owned_id: str | None = None,
    check_owner: bool = False,
) -> SyncReport:
    """Make the collection hold the entries. With apply=False only work out what would change.

    Titles are added first, then stale ones removed (at most MAX_REMOVALS_PER_ROUND), so a failure part-way
    never leaves the collection shorter than it was. An empty collection is never created.

    Two more guards (see docs/how-it-works.md): nothing is removed when the wanted list is empty or less than
    half of what the collection holds, unless ALLOW_SHRINK=true; and with `check_owner` a collection that this
    tool did not make (`owned_id` differs) is left completely alone unless ADOPT=true.
    """
    want = [e.item_id for e in entries]
    report = SyncReport(collection=jf.find_collection(cfg.collection_name))
    if report.collection is None:
        if not want:
            return report
        report.created = True
        report.added = want
        if apply:
            report.collection = jf.create_collection(cfg.collection_name, want)
        return report
    if check_owner and not cfg.adopt and owned_id != report.collection:
        report.foreign = True
        return report
    have = jf.collection_members(report.collection)
    d = core.diff(have, want, cfg.max_removals_per_round, keep_ids)
    report.added, report.removed, report.held_back = list(d.add), list(d.remove), list(d.held_back)
    kept_total = len(set(want) | set(keep_ids))
    if have and not cfg.allow_shrink and (kept_total == 0 or kept_total * 2 < len(have)) and report.removed:
        report.shrink_blocked = True
        report.held_back = report.removed + report.held_back
        report.removed = []
    if apply:
        jf.add_to_collection(report.collection, report.added)
        jf.remove_from_collection(report.collection, report.removed)
    return report
