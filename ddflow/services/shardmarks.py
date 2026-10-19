"""A local high-water mark per event shard, so a lost event is VISIBLE (Ba24a8d32ee).

Events live in git history only. `git push -f` of an older main, a revert of the events
commit, a `reset --hard` or a deleted shard takes events out of the tree, and nothing noticed:
`show` said open, `doctor` Healthy, and the only trace was the reflog. Torn lines, conflict
markers and hand edits are detected; the MISSING-event class was blind.

`.ddflow/local/shard-marks` (a LocalStore document: machine-local, never committed) holds one row
per (branch, shard): `{shard, ref, count, last_event_id, size}` -- how many events (whole
JSON-object lines) this clone last saw in that shard ON that branch, the id of the last one, and the file
size at that look. A mark is compared only on the branch it was taken on, so checking out a
branch that never had the events is not a loss, and coming back to the original branch still
compares against what was seen there. `observe` raises marks and never lowers them; `lost`
reports the shards now holding fewer events than the mark. Nothing is rewritten or repaired: the
operator is told where the events may still be, and `accept` (``ddflow doctor
--accept-lost-events``) records the new counts once they have decided.

A row's branch and shard come from its KEY. An older build keyed a row by the plain shard name
with a `ref` and no `shard` (B21968d4ea4); `_migrate` folds such a row into its scoped key on
every read and write, never lowering a mark. What a shard holds is what the working tree holds
(what the fold reads): uncommitted appends and untracked shards are not a loss.

Known limit: the mark is a count and the last event's id, not the set of ids. A rewrite that
removes an interior event AND brings in enough new ones to reach the old count with the old
last event still present, all before any ddflow command looks at the shard, cannot be told
from growth. Closing that needs a per-event record (the P-MULTIUSER sync-check, not this fix).

Everything here fails SAFE: a missing mark file has nothing to compare, an unreadable one is
replaced, a newer-schema one is left alone; none of them raises into the caller. ddflow itself
only appends to a shard, so its own operations never shrink one. No event, fold or payload
changes (D-compat).
"""

from __future__ import annotations

import contextlib
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..infra import fsio
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
        if self.now >= self.was:  # grown past the mark since, but the event it saw is gone
            head = (
                f"{self.shard} no longer holds the event this clone last saw in it "
                f"(it held {self.was}, now {self.now}){seen}"
            )
        else:
            head = (
                f"{self.shard} lost {self.was - self.now} event(s) since this clone last saw it "
                f"(was {self.was}, now {self.now}){seen}"
            )
        return (
            f"{head}. They may still be in git: `git reflog`, "
            f"`git log --all -- {path}`, `git show <sha>:{path}`. Nothing was rewritten; "
            "`ddflow doctor --accept-lost-events` records the new count once you have decided"
        )


def _store(log: Any) -> LocalStore:
    return LocalStore(Path(log.root) / ".ddflow" / "local")


def _scan(path: Path) -> tuple[int, str, int] | None:
    """(event lines, id of the last one, size) of one shard; None when unreadable."""
    try:
        data = path.read_bytes()
    except OSError:
        return None
    count, last = 0, ""
    for whole in data.splitlines():
        raw = whole.strip()
        # an event line is a whole JSON object with an id and a kind; a conflict marker or a torn fragment is not
        # one, and counting it would turn its later removal into a "loss"
        if not (
            raw.startswith(b"{") and raw.endswith(b"}") and b'"id"' in raw and b'"kind"' in raw
        ):
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


def _head_ref(root: Any) -> str:
    """The branch this tree is on (git's HEAD ref, `refs/heads/x`), "" when detached or
    unknown -- and then no mark is taken or compared, since every detached state would share
    one slot. Read from git's HEAD file, no subprocess: a mark is only compared on the branch it was taken on, so
    switching to a branch that never had those events is not a loss (a reset, a revert and a
    force-push all happen ON a branch)."""
    try:
        git = Path(root) / ".git"
        if git.is_file():
            first = git.read_text("utf-8").strip()
            git = Path(first.removeprefix("gitdir:").strip())
            if not git.is_absolute():
                git = Path(root) / git
        head = (git / "HEAD").read_text("utf-8").strip()
        return head.removeprefix("ref:").strip() if head.startswith("ref:") else ""
    except (OSError, ValueError):
        return ""


def _key(ref: str, shard: str) -> str:
    return f"{ref}\t{shard}"


def _mk(shard: str, scanned: tuple[int, str, int], ref: str) -> dict[str, Any]:
    return {
        "shard": shard,
        "ref": ref,
        "count": scanned[0],
        "last_event_id": scanned[1],
        "size": scanned[2],
    }


def _update(store: LocalStore, fn: Any) -> None:
    """`store.update`, but a torn mark file is replaced by a fresh one instead of leaving the
    feature dark for good (the file holds only counts that the next look rebuilds)."""
    # The plain ignore file the other writers of this directory leave (seen.json's pins "*"):
    # whoever creates `.ddflow/local` first must not change what the others find there.
    fsio.ensure_ignored_dir(store.root)
    try:
        store.update(NAME, fn, schema=MARKS_SCHEMA, default={})
    except StoreUnreadable:
        store.remove(NAME)
        store.update(NAME, fn, schema=MARKS_SCHEMA, default={})


def _tail_lines(path: Path, n: int = 2) -> bytes:
    """The shard's last ``n`` non-blank lines, read from the end (a few KiB, not the file)."""
    with path.open("rb") as f:
        end = f.seek(0, 2)
        pos, buf = end, b""
        while pos > 0:
            step = min(8192, pos)
            pos -= step
            f.seek(pos)
            buf = f.read(step) + buf
            lines = [ln for ln in buf.split(b"\n") if ln.strip()]
            if len(lines) > n or pos == 0:
                return b"\n".join(lines[-n:])
        return b""


def _holds(path: Path | None, event_id: str, *, tail: bool = False) -> bool:
    """Does the shard still contain the event ``event_id``, as an `"id"` field (the shape of
    an event line; no payload quotes an event id under an `id` key)? ``tail`` asks only of
    the last two lines: the append path runs after the new event is written, so the one it
    asks about is the one before it. True when there is nothing to ask (no id recorded) or
    the file cannot be read (could not tell is not a loss)."""
    if not event_id:
        return True
    if path is None:
        return False
    try:
        data = _tail_lines(path) if tail else path.read_bytes()
    except OSError:
        return True
    return re.search(rb'"id"\s*:\s*"' + re.escape(event_id.encode()) + rb'"', data) is not None


#: How long a look that finds a shard SHORT is repeated before it is believed: git rewrites a
#: working-tree file in place (a checkout, a merge), and a reader in that span sees half of it.
#: The flag is sticky, so one such look flagged a loss that was never one (B-events-uncommitted-shards).
SETTLE_LOOKS = 4
SETTLE_S = 0.1


def _settled(
    path: Path | None, old: dict[str, Any], scanned: tuple[int, str, int]
) -> tuple[int, str, int]:
    """``scanned``, looked at again while it reads as a shrink against ``old``: a shard being
    rewritten by another process settles within a moment; a real loss is still there."""
    seen = str(old.get("last_event_id", ""))
    for _ in range(SETTLE_LOOKS):
        if path is None or not (scanned[0] < old["count"] or not _holds(path, seen)):
            break
        time.sleep(SETTLE_S)
        again = _scan(path)
        if again is None:
            break
        scanned = again
    return scanned


def _raised(
    out: dict[str, Any], shard: str, scanned: tuple[int, str, int], ref: str, path: Path | None
) -> None:
    """Record ``scanned`` as this branch's mark of ``shard``, unless that would lower it or
    would forget a loss. A shard that holds fewer events than the mark, or no longer holds the
    event the mark saw, is flagged ``shrunk`` and the mark is kept as it was: growth by new
    appends cannot clear it (an interior event may be gone while the tail and the count are
    back). It clears when the shard is exactly what the mark saw (the events were restored) or
    is `accept`ed."""
    key = _key(ref, shard)
    old = _row(out, key)
    seen = str(old.get("last_event_id", "")) if old else ""
    if old is None:
        out[key] = _mk(shard, scanned, ref)
    elif old.get("shrunk"):
        if scanned[2] == old.get("size") and scanned[0] >= old["count"] and _holds(path, seen):
            out[key] = _mk(shard, scanned, ref)  # restored to exactly what was seen
    elif scanned[0] < old["count"] or not _holds(path, seen):
        scanned = _settled(path, old, scanned)
        if scanned[0] < old["count"] or not _holds(path, seen):
            out[key] = {**old, "shrunk": True}
        else:
            out[key] = _mk(shard, scanned, ref)
    else:
        out[key] = _mk(shard, scanned, ref)


def _migrate(marks: Any) -> dict[str, Any]:
    """The marks in the one current shape: a row per ``"<ref>\t<shard>"`` key, whose ``ref`` and
    ``shard`` fields are taken FROM THE KEY (a row that lacks them, or disagrees, is not trusted
    for them). An older build keyed a row by the plain shard name with a ``ref`` field and no
    ``shard`` field; read as a row of its branch it named no shard and reported a total loss
    (B21968d4ea4). Such a row is folded into its scoped key, keeping the higher count (a mark is
    never lowered; the scoped one wins a tie, and a scoped row already flagged ``shrunk`` stays
    as it is). A plain row with no ``ref`` names no branch to compare on and a row that is not a mark at all says nothing: both are left as they were."""
    if not isinstance(marks, dict):
        return {}
    out: dict[str, Any] = {}
    plain: list[tuple[str, dict[str, Any]]] = []
    for key, row in marks.items():
        if not (isinstance(key, str) and isinstance(row, dict)):
            out[key] = row
        elif "\t" in key:
            ref, shard = key.split("\t", 1)
            if ref and shard and _row(marks, key) is not None:
                out[key] = {**row, "shard": shard, "ref": ref}
            else:
                out[key] = row  # not a mark: ignored on read
        elif isinstance(row.get("ref"), str) and row["ref"] and _row(marks, key) is not None:
            plain.append((key, row))
        else:
            out[key] = row
    for shard, row in plain:
        scoped = _key(row["ref"], shard)
        old = _row(out, scoped)
        if old is None or (row["count"] > old["count"] and not old.get("shrunk")):
            out[scoped] = {**row, "shard": shard}
    return out


def _read(store: LocalStore) -> dict[str, Any] | None:
    """The marks (see `_migrate`); {} when there are none or the file is torn (started afresh);
    None when a NEWER ddflow wrote it (left alone: neither compared against nor overwritten)."""
    try:
        data = store.read(NAME, schema=MARKS_SCHEMA, default={})
    except StoreUnreadable:
        return {}
    except Exception:
        return None
    return _migrate(data)


def _row(marks: dict[str, Any], shard: str) -> dict[str, Any] | None:
    row = marks.get(shard)
    if isinstance(row, dict) and isinstance(row.get("count"), int):
        return row
    return None


def observe(repo: Any, log: Any) -> None:
    """Raise this branch's marks to what the shards hold now. Cheap: only a shard whose size
    differs from the last record is read. Never raises, never lowers a mark."""
    try:
        store = _store(log)
        marks = _read(store)
        if marks is None:
            return
        ref = _head_ref(log.root)
        if not ref:
            return  # detached: no branch to compare on (see `_head_ref`)
        found: dict[str, tuple[int, str, int]] = {}
        paths = {p.name: p for p in log.shards()}
        for p in log.shards():
            try:
                size = p.stat().st_size
            except OSError:
                continue
            row = _row(marks, _key(ref, p.name))
            if row is not None and row.get("size") == size and not row.get("shrunk"):
                continue
            if (scanned := _scan(p)) is not None:
                found[p.name] = scanned
        if not found:
            return

        def raise_marks(cur: Any) -> dict[str, Any]:
            out = _migrate(cur)
            for name in found:
                # looked at again HERE, under the marks lock: an append by another process
                # between the unlocked look above and this write has already raised the mark,
                # and a stale count must not read as a shrink
                fresh = _scan(paths[name]) if name in paths else None
                if fresh is not None:
                    _raised(out, name, fresh, ref, paths[name])
            return out

        _update(store, raise_marks)
    except Exception:
        return


def appended(log: Any, event: Any, before: int, after: int) -> None:
    """The mark follows an append (called by the log under its lock): +1 and the new last id
    when the mark stood exactly at the shard's size before the write, else a full look.
    Without this the events of a clone's LAST command were never in a mark, and losing
    them was the case the mark exists for."""
    name = log.shard.name
    ref = _head_ref(log.root)
    if not ref:
        return  # detached: no branch to compare on

    def bump(cur: Any) -> dict[str, Any]:
        out = _migrate(cur)
        row = _row(out, _key(ref, name))
        if (
            row is not None
            and not row.get("shrunk")
            and row.get("size") == before
            and _holds(log.shard, str(row.get("last_event_id", "")), tail=True)
        ):
            out[_key(ref, name)] = _mk(name, (row["count"] + 1, event.id, after), ref)
        elif (scanned := _scan(log.shard)) is not None:
            _raised(out, name, scanned, ref, log.shard)
        return out

    store = _store(log)
    if _read(store) is None:
        return  # a newer ddflow's file: left alone
    _update(store, bump)


def _rows_here(marks: dict[str, Any], ref: str) -> list[dict[str, Any]]:
    """The rows taken on branch ``ref``, by shard name."""
    rows = [r for k in marks if (r := _row(marks, k)) is not None and r.get("ref") == ref]
    return sorted(rows, key=lambda r: str(r.get("shard", "")))


def lost(repo: Any, log: Any) -> list[Lost]:
    """The shards holding fewer events than this clone last saw in them on this branch (a
    deleted shard holds none). Empty when there is no mark to compare against."""
    try:
        marks = _read(_store(log))
        if not marks:
            return []
        present = {p.name: p for p in log.shards()}
        out: list[Lost] = []
        ref = _head_ref(log.root)
        if not ref:
            return []  # detached HEAD (a bisect, a rebase, a look at a sha): nothing to compare
        for row in _rows_here(marks, ref):
            name = str(row.get("shard", ""))
            scanned = _scan(present[name]) if name in present else (0, "", 0)
            if scanned is None:
                continue  # unreadable: the integrity pass names it
            seen = str(row.get("last_event_id", ""))
            if (
                row.get("shrunk")
                or scanned[0] < row["count"]
                or not _holds(present.get(name), seen)
            ):
                out.append(Lost(name, row["count"], scanned[0], seen))
        return out
    except Exception:
        return []


def accept(repo: Any, log: Any) -> list[Lost]:
    """Record the shards' present counts as this branch's marks (the operator has seen the
    loss). Returns what was recorded."""
    gone = lost(repo, log)
    if not gone:
        return []
    present = {p.name: p for p in log.shards()}
    ref = _head_ref(log.root)
    done: set[str] = set()

    def lower(cur: Any) -> dict[str, Any]:
        out = _migrate(cur)
        for g in gone:
            p = present.get(g.shard)
            scanned = _scan(p) if p is not None else (0, "", 0)
            if scanned is None:
                continue
            done.add(g.shard)
            if p is None:
                out.pop(_key(ref, g.shard), None)
            else:
                out[_key(ref, g.shard)] = _mk(g.shard, scanned, ref)
        return out

    try:
        _update(_store(log), lower)
    except Exception:
        return []  # nothing recorded: the PROBLEM stands, and doctor must still report
    return [g for g in gone if g.shard in done]  # only what was recorded as accepted


if appended not in APPEND_HOOKS:
    APPEND_HOOKS.append(appended)
