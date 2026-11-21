"""The small JSON file that remembers things between rounds.

- `kept`: titles somebody asked to keep longer ({item_id: {"title", "at", "until"}}). A title is kept at most
  once per KEEP_DAYS, judged from `at`.
- `alerted`: titles already reported as overdue, so a title is reported once.
- `owned`: the id of the collection this tool made (or was told to adopt). Without it, an existing collection of
  the same name is left alone.

A missing or damaged file means "start fresh". That can repeat an overdue alert, forget a keep note and make
the tool treat its own collection as foreign (it then changes nothing until ADOPT=true); it never changes
the collection by itself.
"""

import contextlib
import fcntl
import json
import os
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any


class StateError(Exception):
    """The state file could not be written."""


def empty() -> dict[str, Any]:
    return {"kept": {}, "alerted": [], "owned": None}


def load(path: str) -> dict[str, Any]:
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return empty()
    out = empty()
    if isinstance(data, dict):
        if isinstance(data.get("kept"), dict):
            out["kept"] = {k: v for k, v in data["kept"].items() if isinstance(v, dict)}
        if isinstance(data.get("alerted"), list):
            out["alerted"] = [x for x in data["alerted"] if isinstance(x, str)]
        if isinstance(data.get("owned"), str):
            out["owned"] = data["owned"]
    return out


def save(path: str, state: dict[str, Any]) -> None:
    tmp = ""
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".state-", suffix=".tmp", dir=str(Path(path).parent))
        with os.fdopen(fd, "w") as handle:
            handle.write(json.dumps(state))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except OSError as e:
        if tmp:
            Path(tmp).unlink(missing_ok=True)
        raise StateError(
            f"cannot write {path}: {e}. Mount a writable folder at /data or change LOMBO_FAREWELL_STATE_FILE."
        ) from e


@contextlib.contextmanager
def locked(path: str) -> Iterator[None]:
    """Hold an exclusive lock while a round or a `keep` runs, so two of them never overlap.

    The lock file sits beside the state file. If the folder cannot be written the lock is skipped: the same
    problem is reported by `save` when it matters.
    """
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        handle = open(path + ".lock", "w")  # noqa: SIM115  # closed in the finally below
    except OSError:
        yield
        return
    try:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield
    finally:
        handle.close()  # closing releases the lock
