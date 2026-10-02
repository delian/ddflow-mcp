"""Waiting on another agent's claim — and being woken the moment it ends.

Before this, an agent refused by a conflict ("globs overlap B16 held by agent-a") had
two moves: poll `next` on a timer it had to invent, or stop and wait for a person to
say "try again". Both were observed on this repository's own queue: six items sat
blocked behind one lease while their agents idled, and the only thing that ever resumed
them was an operator reading the board. Nothing recorded that anyone was waiting, so the
holder -- the one agent who could have finished sooner, or narrowed its globs -- never
knew it was holding anyone up.

Two halves, deliberately asymmetric:

* **The waiter watches the log.** Every release, completion and abandonment is an event,
  so "has anything changed?" is `EventLog.extent()` -- one `stat` per shard, no reads --
  and the state is re-folded only when it has. A lease that simply EXPIRES appends
  nothing, so the state is also re-checked every `RECHECK_S` regardless.
* **The holder is told, not interrupted.** Waiters register in `.ddflow/local/waits/`,
  which is gitignored: a wait is a fact about a running process on this machine, not a
  fact about the project, and committing it would replay a wait nobody is doing. The
  holder's `heartbeat`, `release` and `complete` read the registry and name who is
  waiting, so the holder learns it at the moments it is already talking to ddflow.

A registration is live only while its process is and its deadline has not passed. A
waiter killed mid-wait leaves a file that the next reader prunes; it can never make a
holder believe someone is waiting who is not.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import secrets
import socket
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..infra.tomlcfg import atomic_write

#: Where waits are registered. Under `.ddflow/local/`, which carries its own `*`
#: `.gitignore` -- see `infra.log._clone_suffix`.
WAITS_DIR = Path(".ddflow") / "local" / "waits"

#: Seconds between `stat` passes over the log shards. Cheap enough to be short; a
#: waiter that notices a release two seconds late has lost nothing.
POLL_S = 2.0

#: Seconds between full re-evaluations when the log has NOT grown. A lease that expires
#: appends no event, so without this a waiter behind a crashed holder would sleep until
#: its own deadline.
RECHECK_S = 30.0


@dataclass
class Waiter:
    """One registered wait: who, for what, blocked on which items, until when."""

    agent: str
    item: str = ""  #: "" = waiting for ANYTHING to become ready
    phase: str = ""
    waiting_on: list[str] = field(default_factory=list)
    reason: str = ""
    since: float = 0.0
    until: float = 0.0
    pid: int = 0
    host: str = ""
    #: Deadline-only: valid until ``until`` whatever its process does. Set when a wait
    #: WAKES (the process then exits, and the agent claims in another one) and for a
    #: place in line held by a refused `claim` (no process to watch). This is the
    #: reservation: a place in line that outlives the process that earned it, but only
    #: for a bounded time.
    woken: bool = False
    path: str = ""  #: the registration file; not serialised

    def live(self, now: float | None = None) -> bool:
        """Past its deadline, or its process gone: not waiting, whatever the file says."""
        now = time.time() if now is None else now
        if self.until and now > self.until:
            return False
        if self.woken:
            return bool(self.until)  # no process to fall back on: only a deadline lapses it
        if self.host and self.host != socket.gethostname():
            # Another machine sharing the checkout (NFS). Its pid means nothing here, so
            # the deadline is the only evidence -- and it has not passed.
            return True
        return _pid_alive(self.pid)

    def summary(self, now: float | None = None) -> dict[str, object]:
        now = time.time() if now is None else now
        return {
            "agent": self.agent,
            "item": self.item,
            "phase": self.phase,
            "waiting_on": list(self.waiting_on),
            "waiting_s": round(max(0.0, now - self.since)),
        }


def _pid_alive(pid: int) -> bool:
    # `jobs.alive` is the one liveness test (it also sees through zombies). pid 0 is
    # guarded here because `kill(0, 0)` signals the whole process GROUP and succeeds.
    from .jobs import alive

    return pid > 0 and alive(pid)


def _dir(repo: Path) -> Path:
    return Path(repo) / WAITS_DIR


def register(repo: Path, w: Waiter) -> Waiter:
    """Record ``w`` and return it with its file path set. Written whole, then renamed,
    so a reader never sees half a registration.

    Advisory: where the registry cannot be written (a read-only checkout), the wait
    still runs -- only the holder goes untold -- so this never raises for that.
    """
    d = _dir(repo)
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        return w
    ignore = d.parent / ".gitignore"
    if not ignore.exists():
        with contextlib.suppress(OSError):
            ignore.write_text("*\n", "utf-8")
    w.pid = w.pid or os.getpid()
    w.host = w.host or socket.gethostname()
    w.since = w.since or time.time()
    if not w.path:
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in w.agent)
        w.path = str(d / f"{safe}-{w.pid}-{secrets.token_hex(3)}.json")
    _write(w)
    return w


def update(w: Waiter, *, waiting_on: list[str], reason: str) -> None:
    """Re-point a registration: what blocks a waiter can change while it waits."""
    if waiting_on == w.waiting_on and reason == w.reason:
        return
    w.waiting_on, w.reason = list(waiting_on), reason
    _write(w)


def _write(w: Waiter) -> None:
    if not w.path:
        return  # never registered: the registry was not writable
    body = {k: v for k, v in asdict(w).items() if k != "path"}
    with contextlib.suppress(OSError):
        atomic_write(Path(w.path), json.dumps(body))


def unregister(w: Waiter) -> None:
    if w.path:
        with contextlib.suppress(OSError):
            Path(w.path).unlink()


def mark_woken(w: Waiter, window_s: float) -> None:
    """Keep ``w``'s place in line for ``window_s`` more seconds, whatever its process does.

    A wait returns the moment its item is claimable and exits; the agent claims a moment
    later, from another process. Dropping the registration at the return would leave that
    gap -- the very moment the queue exists for -- open to whoever polls first.
    """
    if not w.path:
        return
    # Not waiting any more, so no holder is told it holds this agent up.
    w.woken, w.waiting_on, w.until = True, [], time.time() + window_s
    _write(w)


def _queue_path(repo: Path, agent: str, item: str) -> Path:
    # Sanitised for the filesystem, then keyed by a hash of the exact pair: two pairs that
    # sanitise alike ("a/b" and "a_b") must not share one place in line.
    digest = hashlib.sha1(
        f"{agent}\0{item}".encode("utf-8", "surrogateescape"), usedforsecurity=False
    ).hexdigest()[:10]
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in f"{agent}-q-{item}")
    return _dir(repo) / f"{safe[:80]}-{digest}.json"


def queue(
    repo: Path, agent: str, item: str, *, waiting_on: list[str], reason: str, window_s: float
) -> None:
    """Hold ``agent``'s place in line for ``item`` after a refused claim.

    A claim that is refused and tried again is a waiter that never typed `wait`: it polls.
    Its place is its FIRST refusal; each refusal renews the deadline, so it stays in line
    while it keeps asking and loses the place ``window_s`` after it stops. Advisory: an
    unwritable registry means no queue, never a failed claim.
    """
    path = _queue_path(repo, agent, item)
    now = time.time()
    since = now
    with contextlib.suppress(OSError, ValueError, TypeError, KeyError):
        old = json.loads(path.read_text("utf-8"))
        if float(old["until"]) > now:  # a lapsed place is gone: the next refusal re-joins
            since = float(old["since"])
    # A place the agent already holds for this item (a live `wait`, or a wait that woke
    # and has not been claimed on yet) is the place: renew it, never start a younger one.
    for w in live_waiters(repo, now):
        if w.agent == agent and w.item == item:
            if w.woken:
                w.until = now + window_s
                _write(w)
            return
    w = Waiter(
        agent=agent,
        item=item,
        waiting_on=list(waiting_on),
        reason=reason,
        since=since,
        until=now + window_s,
        woken=True,
        path=str(path),
    )
    w.host = socket.gethostname()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        ignore = path.parent.parent / ".gitignore"
        if not ignore.exists():
            ignore.write_text("*\n", "utf-8")
    except OSError:
        return
    _write(w)


def clear(repo: Path, agent: str, item: str) -> None:
    """``agent`` got ``item``: every place in line it held for it is spent."""
    for w in live_waiters(repo):
        if w.agent == agent and w.item == item:
            unregister(w)


def drop_queue(repo: Path, agent: str, item: str) -> None:
    """Remove the place a refused claim holds (a `wait` for the item has taken it over)."""
    with contextlib.suppress(OSError):
        _queue_path(repo, agent, item).unlink()


def take_place(repo: Path, agent: str, item: str) -> float:
    """When ``agent`` joined the line for ``item`` (its earliest live place), or 0.0 for
    "now". A place held by a refused claim is handed to the wait that follows it (the
    wait carries this ``since`` and then `drop_queue`s the old record, in that order, so
    there is no moment with no place on disk), so typing `wait` never sends an agent to
    the back of a line it has stood in for an hour."""
    if not item:
        return 0.0
    since = [w.since for w in live_waiters(repo) if w.agent == agent and w.item == item]
    return min(since) if since else 0.0


def _well_formed(w: Waiter) -> bool:
    """Every field the right type. Checked whole, up front: a field that is only wrong
    when USED (`since` in the sort) once broke the read for every waiter beside it."""

    def num(v: object) -> bool:
        return isinstance(v, (int, float)) and not isinstance(v, bool)

    return (
        all(isinstance(v, str) for v in (w.agent, w.item, w.phase, w.reason, w.host))
        and isinstance(w.waiting_on, list)
        and all(isinstance(i, str) for i in w.waiting_on)
        and num(w.since)
        and num(w.until)
        and isinstance(w.pid, int)
        and not isinstance(w.pid, bool)
        and isinstance(w.woken, bool)
    )


def live_waiters(repo: Path, now: float | None = None) -> list[Waiter]:
    """Every wait still in progress. Dead registrations are pruned as they are found."""
    now = time.time() if now is None else now
    d = _dir(repo)
    if not d.is_dir():
        return []
    out: list[Waiter] = []
    for path in sorted(d.glob("*.json")):
        try:
            raw = json.loads(path.read_text("utf-8"))
            if not isinstance(raw, dict):
                continue  # foreign: valid JSON, but not a registration
            w = Waiter(**{k: v for k, v in raw.items() if k in Waiter.__dataclass_fields__})
            if not _well_formed(w):
                continue  # mistyped (an older format, a hand-written file): skipped whole
            live = w.live(now)
        except (OSError, ValueError, TypeError):
            continue  # torn or foreign; not ours to delete
        w.path = str(path)
        if live:
            out.append(w)
        else:
            with contextlib.suppress(OSError):
                path.unlink()
    return sorted(out, key=lambda x: x.since)


def waiting_on(repo: Path, item: str, now: float | None = None) -> list[dict[str, object]]:
    """Summaries of the live waits that ``item`` is holding up."""
    now = time.time() if now is None else now
    return [w.summary(now) for w in live_waiters(repo, now) if item in w.waiting_on]
