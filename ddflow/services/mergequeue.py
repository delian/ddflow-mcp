"""The merge queue's store: durable entries, their order, and the integrator's lock.

An agent that finished an item enqueues it (`ddflow merge --queue`, `ddflow queue add`) and
goes on; one integrator at a time (`ddflow queue run`) lands the head: it merges main into
the item's branch, regenerates the pins, runs the ci gate once and merges, in the order the
entries were added. This module is the part that needs no git and no gate: the entries, the
order they are taken in, the estimate of when each lands, and the lock that keeps two
integrators apart (`api/mergequeue_run.py` is the integrator).

State lives in `.ddflow/local/mergequeue/`, one JSON file per entry named by its sequence
number, beside the lock. It is a fact about this machine's checkout, like a wait
(`services/waits.py`): the item, its gates and its merge are in the event log; the queue only
orders the work of landing it. A parked entry stays on the record with the reason it was
parked, until it is added again or removed.
"""

from __future__ import annotations

import contextlib
import json
import os
import time
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

from ..infra import fsio
from . import gates as G
from .completion import changed_paths
from .jobs import alive, proc_start

#: Where the queue is kept. Under `.ddflow/local/`, which is never committed.
QUEUE_DIR = Path(".ddflow") / "local" / "mergequeue"

QUEUED, RUNNING, MERGED, PARKED = "queued", "running", "merged", "parked"
STATES = (QUEUED, RUNNING, MERGED, PARKED)
#: Entries the integrator is finished with (kept for status and the ETA).
FINISHED = (MERGED, PARKED)


@dataclass
class Entry:
    item: str
    seq: int
    agent: str = ""
    state: str = QUEUED
    queued_at: float = 0.0
    started_at: float = 0.0
    finished_at: float = 0.0
    #: Why the entry is parked (or a note on a merged one).
    reason: str = ""
    #: The commit the base points at after the landing, for a merged entry.
    sha: str = ""
    #: How many times the integrator has taken this entry.
    attempts: int = 0

    @property
    def duration_s(self) -> float:
        return max(0.0, self.finished_at - self.started_at) if self.finished_at else 0.0


def _dir(repo: Path) -> Path:
    return Path(repo) / QUEUE_DIR


def _path(repo: Path, seq: int) -> Path:
    return _dir(repo) / f"{seq:08d}.json"


def _write(repo: Path, e: Entry) -> None:
    d = _dir(repo)
    d.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        fsio.ensure_ignored_dir(d)
    fsio.atomic_write(_path(repo, e.seq), json.dumps(asdict(e), sort_keys=True) + "\n")


def _read(path: Path) -> Entry | None:
    """One entry, or None for a file that is not one (half-written by another tool, or
    from a newer ddflow): the queue never fails on a stranger's file."""
    try:
        raw = json.loads(path.read_text("utf-8"))
        known = {f.name for f in fields(Entry)}
        e = Entry(**{k: v for k, v in raw.items() if k in known})
    except (OSError, ValueError, TypeError):
        return None
    ok = isinstance(e.item, str) and isinstance(e.seq, int) and e.state in STATES
    return e if ok else None


def entries(repo: Path) -> list[Entry]:
    """Every entry, in queue order (oldest first)."""
    found = (_read(p) for p in sorted(_dir(repo).glob("[0-9]*.json")))
    return sorted((e for e in found if e is not None), key=lambda e: e.seq)


def live(repo: Path) -> list[Entry]:
    """The entries still to land or landing: queued and running, in order."""
    return [e for e in entries(repo) if e.state in (QUEUED, RUNNING)]


def get(repo: Path, item: str) -> Entry | None:
    """The newest entry for ``item``."""
    mine = [e for e in entries(repo) if e.item == item]
    return mine[-1] if mine else None


def add(
    repo: Path, item: str, *, agent: str, keep_history: int, lock_wait_s: float = 10.0
) -> tuple[Entry, bool]:
    """(the entry, whether it was added). An item already waiting keeps its place; a parked
    or merged one is replaced by a new entry at the back. ``lock_wait_s`` bounds the wait on the
    numbering lock, held only while a directory is read, so only a stuck process reaches it."""
    d = _dir(repo)
    d.mkdir(parents=True, exist_ok=True)
    with fsio.file_lock(d / "add.lock", lock_wait_s):
        every = entries(repo)
        waiting = next((e for e in every if e.item == item and e.state in (QUEUED, RUNNING)), None)
        if waiting is not None:
            return waiting, False
        for old in (e for e in every if e.item == item):
            _path(repo, old.seq).unlink(missing_ok=True)
        seq = max((e.seq for e in every), default=0) + 1
        e = Entry(item=item, seq=seq, agent=agent, queued_at=time.time())
        _write(repo, e)
        _prune(repo, keep_history)
    return e, True


def _prune(repo: Path, keep: int) -> None:
    done = [e for e in entries(repo) if e.state in FINISHED]
    for old in done[: max(0, len(done) - keep)]:
        _path(repo, old.seq).unlink(missing_ok=True)


def save(repo: Path, e: Entry) -> None:
    _write(repo, e)


def remove(repo: Path, item: str) -> Entry | None:
    """Drop ``item``'s entry (any state but running); the entry removed, or None."""
    e = get(repo, item)
    if e is None or e.state == RUNNING:
        return None
    _path(repo, e.seq).unlink(missing_ok=True)
    return e


def head(repo: Path) -> Entry | None:
    """The next entry to integrate: the oldest queued one."""
    return next((e for e in entries(repo) if e.state == QUEUED), None)


def take(repo: Path, e: Entry) -> Entry:
    e.state, e.started_at, e.finished_at, e.reason = RUNNING, time.time(), 0.0, ""
    e.attempts += 1
    _write(repo, e)
    return e


def finish(repo: Path, e: Entry, state: str, *, reason: str = "", sha: str = "") -> Entry:
    e.state, e.finished_at, e.reason, e.sha = state, time.time(), reason, sha
    _write(repo, e)
    return e


def requeue_orphans(repo: Path) -> list[str]:
    """Entries left `running` by an integrator that died: queued again, at their place.
    Only called by the integrator that holds the lock, so a running entry is nobody's."""
    out = []
    for e in entries(repo):
        if e.state == RUNNING:
            e.state, e.started_at = QUEUED, 0.0
            e.reason = "an integrator stopped while landing this; taken again"
            _write(repo, e)
            out.append(e.item)
    return out


# -- the integrator's lock ---------------------------------------------------------------


def _info(repo: Path) -> Path:
    return _dir(repo) / "integrator.json"


@contextlib.contextmanager
def integrator(repo: Path, *, agent: str):
    """Hold the integrator lock for the block, yielding True; yield False instead when
    another integrator holds it. The kernel releases the lock with its process, so a crashed
    integrator never wedges the queue."""
    d = _dir(repo)
    d.mkdir(parents=True, exist_ok=True)
    with contextlib.ExitStack() as stack:
        try:
            stack.enter_context(fsio.file_lock(d / "integrator.lock", 0))
        except fsio.LockTimeout:
            yield False
            return
        info = {
            "pid": os.getpid(),
            "started": proc_start(os.getpid()),
            "agent": agent,
            "since": time.time(),
        }
        fsio.atomic_write(_info(repo), json.dumps(info) + "\n")
        stack.callback(lambda: _info(repo).unlink(missing_ok=True))
        yield True


def holder(repo: Path) -> dict[str, Any] | None:
    """Who is integrating right now, or None. Read from the info file the integrator keeps
    while it holds the lock, not by trying the lock: a status check that took the lock for an
    instant would make an integrator starting at that moment think another one was running.
    A crashed integrator's leftover file names a process that is gone (or one that is not the
    same process any more), so it is no holder."""
    try:
        info = json.loads(_info(repo).read_text("utf-8"))
        pid = info["pid"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if not (isinstance(pid, int) and pid > 0 and alive(pid)):
        return None
    started = str(info.get("started", ""))
    now = proc_start(pid)
    if started and now and started != now:
        return None
    return {
        "pid": pid,
        "agent": str(info.get("agent", "")),
        "since": float(info.get("since", 0) or 0),
        "alive": True,
    }


# -- status ----------------------------------------------------------------------------


def typical_s(rows: list[Entry], *, window: int, default_s: int) -> float:
    """Seconds one entry usually takes: the mean of the last ``window`` merged entries."""
    took = [e.duration_s for e in rows if e.state == MERGED and e.duration_s > 0]
    took = took[-window:] if window > 0 else []
    return sum(took) / len(took) if took else float(default_s)


def board(repo: Path, *, window: int, default_s: int, now: float | None = None) -> list[dict]:
    """One row per entry, in queue order, with where it stands and when it should land."""
    now = time.time() if now is None else now
    rows = entries(repo)
    each = typical_s(rows, window=window, default_s=default_s)
    out: list[dict] = []
    ahead = 0.0
    place = 0
    for e in rows:
        row: dict[str, Any] = {
            "item": e.item,
            "seq": e.seq,
            "state": e.state,
            "agent": e.agent,
            "reason": e.reason,
            "sha": e.sha,
            "attempts": e.attempts,
            "waited_s": round(max(0.0, (e.started_at or e.finished_at or now) - e.queued_at), 1),
            "position": 0,
            "eta_s": 0,
        }
        if e.state in (QUEUED, RUNNING):
            place += 1
            row["position"] = place
            if e.state == RUNNING:
                left = max(0.0, each - (now - e.started_at))
            else:
                left = each
            ahead += left
            row["eta_s"] = round(ahead)
        out.append(row)
    return out


# -- what an entry needs before the integrator may take it ---------------------------------


def owed_gates(repo: Path, cfg, st, it, *, unit_tests: bool) -> list[str]:
    """The gates of ``it``'s pipeline that must carry a settled outcome before the integrator
    runs it, each with what it carries now: every gate but the ones the integrator itself
    produces (ci on the merged result, the merge, and unit_tests when it runs them).
    Silence and a failure both leave a gate owed -- a queued merge never skips one."""
    defs = G.load_gates(repo, cfg)
    s = G.status(st, cfg, it.id, defs, diff=lambda: changed_paths(repo, it))
    mine = {"ci", "merge"} | ({"unit_tests"} if unit_tests else set())
    return [
        f"{g} ({it.gate_outcome(g) or 'no outcome'})"
        for g in s.pipeline
        if g not in mine and g not in s.done and g not in s.not_applicable
    ]
