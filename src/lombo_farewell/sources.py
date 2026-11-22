"""Where "scheduled for deletion" comes from. Two adapters, same shape:

- `MaintainerrSource`: reads Maintainerr's active collections; each title is deleted `deleteAfterDays`
  after it was added there. It can postpone a title through Maintainerr's own endpoint.
- `FileSource`: reads a JSON file or a plain-text list written by any other cleanup tool or by hand.
  It is read-only: it cannot postpone anything, so "keep it longer" is refused for it.

A source that cannot be read raises `SourceError`; the caller then leaves the collection alone.
A half-read list is never returned.
"""

import json
import math
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from . import httpjson
from .core import DAY, Candidate


class SourceError(Exception):
    """The list of titles could not be read. Nothing must be changed because of it."""


def parse_when(value: Any) -> float:
    """A Unix time, an ISO date ('2026-12-01') or an ISO date and time. Date-only means midnight UTC."""
    if isinstance(value, bool):
        raise ValueError("not a date")
    if isinstance(value, int | float):
        return _sane(float(value))
    text = str(value).strip().replace("Z", "+00:00")
    try:
        return datetime.combine(date.fromisoformat(text), datetime.min.time(), UTC).timestamp()
    except ValueError:
        stamp = datetime.fromisoformat(text)
        return (stamp if stamp.tzinfo else stamp.replace(tzinfo=UTC)).timestamp()


MAX_TIME = 32_503_680_000.0  # the year 3000; anything beyond (or not a number) is a mistake in the source
MAX_GRACE_DAYS = 36_500.0


def _sane(value: float) -> float:
    if not math.isfinite(value) or not -MAX_TIME < value < MAX_TIME:
        raise ValueError("not a usable time")
    return value


def maintainerr_candidates(collections: Any, media: dict[Any, Any]) -> list[Candidate]:
    """Everything the active Maintainerr collections will delete. Entries missing an id or a date are skipped."""
    if not isinstance(collections, list):
        raise SourceError("Maintainerr: the collection list is not a list")
    out: list[Candidate] = []
    for coll in collections:
        grace = coll.get("deleteAfterDays") if isinstance(coll, dict) else None
        if not isinstance(coll, dict) or not coll.get("isActive") or isinstance(grace, bool):
            continue
        if not isinstance(grace, int | float) or not math.isfinite(grace) or not 0 <= grace <= MAX_GRACE_DAYS:
            continue
        items = media.get(coll.get("id"), [])
        if not isinstance(items, list):
            raise SourceError("Maintainerr: a collection's media list is not a list")
        for m in items:
            if not isinstance(m, dict) or not m.get("mediaServerId") or not m.get("addDate"):
                continue
            try:
                added = parse_when(m["addDate"])
            except ValueError:
                continue
            out.append(
                Candidate(
                    item_id=str(m["mediaServerId"]),
                    title=None,
                    delete_at=added + grace * DAY,
                    grace_days=float(grace),
                    group=str(coll.get("id")),
                    media_id=str(m["mediaServerId"]),
                    group_name=coll["title"] if isinstance(coll.get("title"), str) and coll["title"] else None,
                )
            )
    return out


class MaintainerrSource:
    can_postpone = True

    def __init__(self, base_url: str, timeout: float = 15):
        self.base = base_url.rstrip("/")
        self.timeout = timeout

    def _call(self, method: str, path: str, body: Any = None) -> Any:
        try:
            return httpjson.request(method, self.base + path, path, {}, body, self.timeout)
        except httpjson.HttpError as e:
            raise SourceError(f"Maintainerr {e}") from e

    def fetch(self) -> list[Candidate]:
        collections = self._call("GET", "/api/collections")
        if not isinstance(collections, list):
            raise SourceError("Maintainerr: /api/collections did not return a list")
        media: dict[Any, Any] = {}
        for coll in collections:
            if isinstance(coll, dict) and coll.get("isActive"):
                media[coll.get("id")] = self._call("GET", f"/api/collections/media/?collectionId={coll.get('id')}")
        return maintainerr_candidates(collections, media)

    def postpone(self, group: str, media_id: str, days: int) -> float:
        """Push one title's deletion back `days` days; returns the new deletion time."""
        body = {"collectionId": int(group) if group.isdigit() else group, "mediaId": media_id, "days": days}
        reply = self._call("POST", "/api/collections/media/postpone", body)
        try:
            return parse_when(reply["deletionDate"])
        except (TypeError, KeyError, ValueError) as e:
            raise SourceError("Maintainerr: the postpone reply has no usable deletionDate") from e


def parse_list(text: str) -> list[Candidate]:
    """JSON (a list of {"id" or "title", "leaves"}) or plain text (one 'Title | 2026-12-01' per line).

    Any line or object that cannot be understood makes the whole file an error: a half-read list could
    unlist titles that should stay.
    """
    stripped = text.strip()
    if stripped.startswith(("[", "{")):
        try:
            data = json.loads(stripped)
        except ValueError as e:
            raise SourceError(f"the list is not valid JSON: {e}") from e
        if isinstance(data, dict):
            data = data.get("items")
        if not isinstance(data, list):
            raise SourceError('the JSON list must be an array, or an object with an "items" array')
        return [_from_object(o, n) for n, o in enumerate(data, 1)]
    out: list[Candidate] = []
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        title, _, when = line.partition("|")
        out.append(_make(title.strip(), None, when.strip() or None, f"line {n}"))
    return out


def _from_object(obj: Any, n: int) -> Candidate:
    if not isinstance(obj, dict):
        raise SourceError(f"entry {n} is not an object")
    return _make(obj.get("title"), obj.get("id"), obj.get("leaves"), f"entry {n}")


def _make(title: Any, item_id: Any, leaves: Any, where: str) -> Candidate:
    title = title.strip() if isinstance(title, str) else None
    item_id = item_id.strip() if isinstance(item_id, str) else None
    if not title and not item_id:
        raise SourceError(f"{where} has no title or id")
    when = None
    if leaves not in (None, ""):
        try:
            when = parse_when(leaves)
        except ValueError:
            raise SourceError(f"{where}: {leaves!r} is not a date (use 2026-12-01)") from None
    return Candidate(item_id=item_id or None, title=title or None, delete_at=when, grace_days=None)


class FileSource:
    can_postpone = False

    def __init__(self, path: str):
        self.path = path

    def fetch(self) -> list[Candidate]:
        try:
            text = Path(self.path).read_text(encoding="utf-8")
        except (OSError, ValueError) as e:  # a missing file is an error, never an empty list
            raise SourceError(f"cannot read {self.path}: {e}") from e
        return parse_list(text)

    def postpone(self, group: str, media_id: str, days: int) -> float:
        raise SourceError("the file source cannot postpone; edit the file instead")
