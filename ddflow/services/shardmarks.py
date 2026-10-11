"""A local high-water mark per event shard, so a lost event is VISIBLE (Ba24a8d32ee).

Events live in git history only. `git push -f` of an older main, a revert of the events
commit, a `reset --hard` or a deleted shard takes events out of the tree, and nothing noticed:
`show` said open, `doctor` Healthy, and the only trace was the reflog. Torn lines, conflict
markers and hand edits are detected; the MISSING-event class was blind.

`.ddflow/local/shard-marks` (a LocalStore document: machine-local, never committed) maps a shard
file name to `{count, last_event_id, size}`: how many events (raw non-blank lines) this clone
last saw in it, the id of the last one, and the file size at that look. `observe` raises
marks and never lowers them; `lost` reports the shards now holding fewer events than the mark.
Nothing is rewritten or repaired: the operator is told where the events may still be, and
`accept` (``ddflow doctor --accept-lost-events``) records the new counts once they have decided.

Everything here fails SAFE: a missing mark file has nothing to compare, an unreadable one is
started afresh, a newer-schema one is left alone; none of them raises into the caller. ddflow
itself only appends to a shard, so its own operations never shrink one. No event, fold or
payload changes (D-compat).
"""

from __future__ import annotations

import contextlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..infra.localstore import LocalStore, StoreUnreadable
from ..infra.log import APPEND_HOOKS

NAME = "shard-marks"
MARKS_SCHEMA = 1


@dataclass(frozen=True, slots=True)
class Lost:
    shard: str
    was: int
    now: int
    last_event_id: str

    def render(self) -> str:
        seen = f"; the last event it saw was {self.last_event_id}" if self.last_event_id else ""
        path = f".ddflow/events/{self.shard}"
        return (
            f"{self.shard} lost {self.was - self.now} event(s) since this clone last saw it "
            f"(was {self.was}, now {self.now}){seen}. They may still be in git: `git reflog`, "
            f"`git log --all -- {path}`, `git show <sha>:{path}`. Nothing was rewritten; "
            "`ddflow doctor --accept-lost-events` records the new count once you have decided"
        )


def _store(log: Any) -> LocalStore:
    return LocalStore(Path(log.root) / ".ddflow" / "local")


def _scan(path: Path) -> tuple[int, str, int] | None:
    """(non-blank lines, id of the last whole one, size) of one shard; None when unreadable."""
    try:
        data = path.read_bytes()
    except OSError:
        return None
    count, last = 0, ""
    for raw in data.splitlines():
        if not raw.strip():
            continue
        count += 1
        last = raw.decode("utf-8", "replace")
    last_id = ""
    if last:
        with contextlib.suppress(ValueError):
            row = json.loads(last)
            if isinstance(row, dict) and isinstance(row.get("id"), str):
                last_id = row["id"]
    return count, last_id, len(data)


def _read(store: LocalStore) -> dict[str, Any] | None:
    """The marks; {} when there are none or the file is torn (started afresh); None when a
    NEWER ddflow wrote it (left alone: neither compared against nor overwritten)."""
    try:
        data = store.read(NAME, schema=MARKS_SCHEMA, default={})
    except StoreUnreadable:
        return {}
    except Exception:
        return None
    return dict(data) if isinstance(data, dict) else {}


def _row(marks: dict[str, Any], shard: str) -> dict[str, Any] | None:
    row = marks.get(shard)
    if isinstance(row, dict) and isinstance(row.get("count"), int):
        return row
    return None


def observe(repo: Any, log: Any) -> None:
    """Raise the marks to what the shards hold now. Cheap: only a shard whose size differs
    from the one last recorded is read. Never raises, never lowers a mark."""
    try:
        store = _store(log)
        marks = _read(store)
        if marks is None:
            return
        found: dict[str, tuple[int, str, int]] = {}
        for p in log.shards():
            try:
                size = p.stat().st_size
            except OSError:
                continue
            row = _row(marks, p.name)
            if row is not None and row.get("size") == size:
                continue
            scanned = _scan(p)
            if scanned is None:
                continue
            if row is None or scanned[0] >= row["count"]:
                found[p.name] = scanned
        if not found:
            return

        def raise_marks(cur: Any) -> dict[str, Any]:
            out = dict(cur) if isinstance(cur, dict) else {}
            for name, (count, last_id, size) in found.items():
                old = _row(out, name)
                if old is None or count >= old["count"]:
                    out[name] = {"count": count, "last_event_id": last_id, "size": size}
            return out

        store.update(NAME, raise_marks, schema=MARKS_SCHEMA, default={})
    except Exception:
        return


def appended(log: Any, event: Any, before: int, after: int) -> None:
    """The mark follows an append (called by the log under its lock): +1 and the new last id
    when the mark stood exactly at the shard's size before the write, else a full look.
    Without this the events of a clone's LAST command were never in a mark, and losing
    them was the case the mark exists for."""
    name = log.shard.name

    def bump(cur: Any) -> dict[str, Any]:
        out = dict(cur) if isinstance(cur, dict) else {}
        row = _row(out, name)
        if row is not None and row.get("size") == before:
            out[name] = {"count": row["count"] + 1, "last_event_id": event.id, "size": after}
            return out
        scanned = _scan(log.shard)
        if scanned is not None and (row is None or scanned[0] >= row["count"]):
            out[name] = {"count": scanned[0], "last_event_id": scanned[1], "size": scanned[2]}
        return out

    store = _store(log)
    marks = _read(store)
    if marks is None:
        return  # a newer ddflow's file: left alone
    store.update(NAME, bump, schema=MARKS_SCHEMA, default={})


def lost(repo: Any, log: Any) -> list[Lost]:
    """The shards holding fewer events than this clone last saw in them (a deleted shard
    holds none). Empty when there is no mark to compare against."""
    try:
        marks = _read(_store(log))
        if not marks:
            return []
        present = {p.name: p for p in log.shards()}
        out: list[Lost] = []
        for name in sorted(marks):
            row = _row(marks, name)
            if row is None:
                continue
            scanned = _scan(present[name]) if name in present else (0, "", 0)
            if scanned is None:
                continue
            if scanned[0] < row["count"]:
                out.append(Lost(name, row["count"], scanned[0], str(row.get("last_event_id", ""))))
        return out
    except Exception:
        return []


def accept(repo: Any, log: Any) -> list[Lost]:
    """Record the shards' present counts as the marks (the operator has seen the loss).
    Returns what was accepted."""
    gone = lost(repo, log)
    if not gone:
        return []
    names = {g.shard for g in gone}
    present = {p.name: p for p in log.shards()}
    done: set[str] = set()

    def lower(cur: Any) -> dict[str, Any]:
        out = dict(cur) if isinstance(cur, dict) else {}
        for name in names:
            p = present.get(name)
            scanned = _scan(p) if p is not None else (0, "", 0)
            if scanned is None:
                continue
            done.add(name)
            if scanned[0] == 0 and p is None:
                out.pop(name, None)
            else:
                out[name] = {"count": scanned[0], "last_event_id": scanned[1], "size": scanned[2]}
        return out

    _store(log).update(NAME, lower, schema=MARKS_SCHEMA, default={})
    return [g for g in gone if g.shard in done]  # only what was recorded as accepted


if appended not in APPEND_HOOKS:
    APPEND_HOOKS.append(appended)
