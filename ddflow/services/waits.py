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
    path: str = ""  #: the registration file; not serialised

    def live(self, now: float | None = None) -> bool:
        """Past its deadline, or its process gone: not waiting, whatever the file says."""
        now = time.time() if now is None else now
        if self.until and now > self.until:
            return False
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
            if not isinstance(w.waiting_on, list) or not all(
                isinstance(i, str) for i in w.waiting_on
            ):
                continue  # mistyped: every holder-side reader tests `item in waiting_on`
            live = w.live(now)  # a mistyped field (an older format) raises here
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
