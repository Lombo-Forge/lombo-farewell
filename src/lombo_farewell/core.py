"""The decisions. Pure: no network, no disk, no clock (time is always passed in), so tests need no fakes.

- `plan()` turns what a source says is about to be deleted into one entry per title, soonest first.
- `diff()` says how to change the Jellyfin collection, and never removes more than a cap in one round.
- `keep_check()` and `find()` decide whether, and for which title, a "keep it longer" request can go ahead.
- `overdue_to_alert()` picks the titles whose deletion is late and have not been reported yet.
Nothing here deletes anything: the app only mirrors and postpones.
"""

import math
from dataclasses import dataclass

DAY = 86400.0
OVERDUE_ALERT_DAYS = 2
SLACK = 60.0  # seconds of grace when comparing a deadline with "now"


def norm_id(item_id: str | None) -> str:
    """Jellyfin ids appear with and without dashes; compare them bare and lower case."""
    return (item_id or "").replace("-", "").lower()


@dataclass(frozen=True)
class Candidate:
    """One title a source says is scheduled for deletion, before it is matched to Jellyfin."""

    item_id: str | None  # Jellyfin item id, when the source knows it
    title: str | None  # a name, when the source gives one instead of an id
    delete_at: float | None  # Unix time of the deletion, when known
    grace_days: float | None  # the normal wait before deletion; None when the source cannot postpone
    group: str | None = None  # the source's own group id (a Maintainerr collection)
    media_id: str | None = None  # the source's own id for the title, used to postpone it


@dataclass(frozen=True)
class Entry:
    """A candidate matched to a Jellyfin item."""

    item_id: str
    title: str
    delete_at: float | None
    days_left: int | None
    overdue_days: int
    can_keep: bool
    group: str | None = None
    media_id: str | None = None

    def __str__(self) -> str:
        if self.delete_at is None:
            when = "date unknown"
        elif self.overdue_days:
            when = f"{self.overdue_days} day(s) overdue"
        else:
            when = f"{self.days_left} day(s) left"
        return f"{self.title} ({when})"


@dataclass(frozen=True)
class Diff:
    add: tuple[str, ...]
    remove: tuple[str, ...]
    held_back: tuple[str, ...]  # stale ids left in place this round because of the removal cap

    @property
    def changes(self) -> bool:
        return bool(self.add or self.remove)


def plan(resolved: list[tuple[Candidate, str, str]], now: float, can_postpone: bool) -> list[Entry]:
    """One entry per title (the soonest deadline wins), soonest first, unknown dates last.

    `resolved` holds (candidate, jellyfin_id, title). A title can be kept only while it is inside
    its normal wait, so a title that was just postponed cannot be postponed again at once.
    """
    best: dict[str, Entry] = {}
    for cand, raw_id, title in resolved:
        item = norm_id(raw_id)
        if not item:
            continue
        left = None if cand.delete_at is None or not math.isfinite(cand.delete_at) else cand.delete_at - now
        grace = cand.grace_days
        entry = Entry(
            item_id=item,
            title=title or item,
            delete_at=cand.delete_at,
            days_left=None if left is None else max(0, math.ceil(left / DAY)),
            overdue_days=0 if left is None or left >= 0 else math.floor(-left / DAY),
            can_keep=bool(can_postpone and left is not None and grace is not None and left <= grace * DAY + SLACK),
            group=cand.group,
            media_id=cand.media_id,
        )
        old = best.get(item)
        if old is None or _key(entry) < _key(old):
            best[item] = entry
    return sorted(best.values(), key=_key)


def _key(e: Entry) -> tuple[int, float, str]:
    return (e.delete_at is None, e.delete_at or 0.0, e.item_id)


def diff(have: set[str], want: list[str], max_removals: int, keep_ids: frozenset[str] = frozenset()) -> Diff:
    """What to add to and remove from the collection so it holds `want`.

    Additions are in the order given. At most `max_removals` titles are removed in one round; the
    rest wait for the next round, so a source that suddenly reports nothing cannot empty the
    collection in one go. `keep_ids` are titles the source still lists but that could not be matched to
    Jellyfin this round (unknown or ambiguous): they are never removed because of that.
    """
    wanted = {norm_id(w) for w in want} | {norm_id(k) for k in keep_ids}
    add = tuple(dict.fromkeys(norm_id(w) for w in want if norm_id(w) not in have))
    stale = sorted(have - wanted)
    return Diff(add, tuple(stale[:max_removals]), tuple(stale[max_removals:]))


def find(entries: list[Entry], query: str) -> tuple[Entry | None, str]:
    """Match a typed title (or Jellyfin id) to one entry. Returns (entry, "") or (None, why)."""
    q = query.strip().lower()
    if not q:
        return None, "no title given"
    exact = [e for e in entries if e.title.lower() == q or e.item_id == norm_id(q)]
    if len(exact) == 1:
        return exact[0], ""
    if len(exact) > 1:
        return None, f"{len(exact)} titles are called {query!r}; use the Jellyfin id instead"
    partial = [e for e in entries if q in e.title.lower()]
    if len(partial) == 1:
        return partial[0], ""
    if partial:
        names = ", ".join(sorted(e.title for e in partial)[:5])
        return None, f"{len(partial)} titles match {query!r} ({names}); be more exact"
    return None, f"{query!r} is not in the leaving soon list"


def keep_check(entry: Entry | None, kept: dict[str, object] | None = None, now: float = 0.0, keep_days: int = 0) -> str:
    """'ok', 'not-leaving' or 'already-kept'.

    `kept` is the state record of an earlier postponement of this title ({"at": time, ...}). A title is kept at
    most once per `keep_days`, whatever the cleanup tool's own numbers say.
    """
    if entry is None:
        return "not-leaving"
    if not entry.can_keep:
        return "already-kept"
    at = kept.get("at") if kept else None
    if isinstance(at, int | float) and not isinstance(at, bool) and now < at + keep_days * DAY - SLACK:
        return "already-kept"
    return "ok"


def clean(text: str) -> str:
    """Make a title safe to print or send: control characters and chat markup become '?'."""
    return "".join("?" if (not c.isprintable() or c in "<>@[]") else c for c in text)


def overdue_to_alert(entries: list[Entry], already: set[str]) -> tuple[list[Entry], set[str]]:
    """Titles overdue by OVERDUE_ALERT_DAYS or more that were not reported yet.

    Returns (to_report, remembered): titles that left the list are forgotten, so a return alerts again.
    """
    live = {e.item_id for e in entries}
    late = [e for e in entries if e.overdue_days >= OVERDUE_ALERT_DAYS and e.item_id not in already]
    return late, (already & live) | {e.item_id for e in late}
