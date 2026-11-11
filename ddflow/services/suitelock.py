"""A host-wide limit on concurrent full test suites (DX-suite-host-lock, first slice).

Two or three CI-parity runs at once took a 192-core host to load 337 (bug B26b5951f2b).
The limit lives in the user's state directory, so every project and every agent of the
user shares it; it is `slots.Slots` (an flock per slot, given back when the holder dies)
plus a note of who holds it, so a refused run can say so. ``[ci].max_concurrent_suites``
(default 1) is how many may run, ``[ci].suite_wait_s`` how long a second one waits.

Waiters are served first in, first out (B1fa77a6832: a bare race for the flock let a fresh
arrival beat one that had waited half an hour). Each waiter takes a TICKET, a file in
``queue/`` named by the arrival time, holds an flock on it (a dead waiter's ticket frees
itself with the process) and touches it as a heartbeat (a waiter that stopped beating is
passed over). Only the head of the live tickets tries the slots. A waiter that gave up on the
wait parks its ticket for ``[ci].suite_queue_grace_s``, so a retry keeps its place.

r-lock-cycle-selfheal forbids AGENTS holding file leases while they wait; this is a bounded
host-lock poll with a heartbeat, 0.25 s apart like the sweep of `slots.Slots.hold`, it ends
after ``[ci].suite_wait_s`` (measured from when the waiter reached the head, once it has, so
time spent advancing up the line does not bounce it) and no lease is held. `SuiteBusy` takes
the queue ``position`` as a third argument (default 0, unknown)."""

from __future__ import annotations

import contextlib
import json
import os
import re
import sys
import time
from collections.abc import Callable, Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

from ..adapters.clock import DEFAULT as CLOCK
from ..adapters.env import DEFAULT as ENV
from ..infra import fsio
from . import slots as SLOTS

NAME = "suites"
QUEUE = "queue"
#: Seconds between two looks of a queued waiter (it also beats its ticket then).
QUEUE_POLL_S = 0.25
#: A ticket this young may be one whose owner has not locked it yet.
NEW_S = 1.0
#: How long a new waiter waits for its own ticket's lock: a scan may hold it for an instant.
LOCK_S = 5.0


class SuiteBusy(RuntimeError):
    """Every suite slot stayed held for the whole wait. Exit 2: could not run.

    ``position`` (new argument, default 0 = unknown) is the caller's place in the queue; the
    message names the holder's pid and says "you are #k in the queue"."""

    def __init__(self, holder: dict[str, Any], waited_s: float, position: int = 0) -> None:
        who = holder.get("holder") or "another run"
        since = holder.get("at_iso") or "an unknown time"
        pid = holder.get("pid")
        by = f"pid {pid} / {who}" if pid else str(who)
        line = f"blocked on {by} since {since}"
        if position:
            line += f", you are #{position} in the queue"
        super().__init__(line)
        self.holder = holder
        self.waited_s = waited_s
        self.position = position


def state_dir() -> Path:
    base = ENV.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "ddflow" / "suites"


def _note(directory: Path, index: int) -> Path:
    return directory / f"{NAME}.{index}.holder"


def _held(directory: Path, index: int) -> bool:
    try:
        with fsio.file_lock(directory / f"{NAME}.{index}.lock", 0):
            return False
    except fsio.LockTimeout:
        return True


def _holders(directory: Path, limit: int) -> dict[str, Any]:
    """The note of the oldest holder among the slots held right now (a crashed holder's note
    outlives it, so a note of a free slot is not read)."""
    notes = []
    for i in range(limit):
        if not _held(directory, i):
            continue
        with contextlib.suppress(OSError, ValueError):
            notes.append(json.loads(_note(directory, i).read_text()))
    return min(notes, key=lambda n: n.get("at", 0), default={})


def running(
    cfg: Any, directory: Path | str | None = None, now: float | None = None
) -> tuple[list[dict[str, Any]], int]:
    """(the suites running now, the waiters queued), read-only: one dict per held slot (holder,
    pid, at, slot, age_s), oldest first, and the count of tickets that beat within
    ``[ci].suite_queue_stale_s``. Nothing is created or removed (a dead ticket is not swept)."""
    directory = Path(directory) if directory else state_dir()
    at = CLOCK.wall() if now is None else now
    rows: list[dict[str, Any]] = []
    for i in range(max(1, int(cfg.ci.max_concurrent_suites))):
        if not _held(directory, i):
            continue
        with contextlib.suppress(OSError, ValueError, TypeError):
            note = json.loads(_note(directory, i).read_text())
            if not isinstance(note, dict):
                continue
            rows.append({**note, "slot": i, "age_s": max(0, int(at - float(note.get("at", at))))})
    queued = 0
    stale = float(cfg.ci.suite_queue_stale_s)
    for path in (directory / QUEUE).glob("*.ticket"):
        with contextlib.suppress(OSError):
            queued += CLOCK.wall() - path.stat().st_mtime <= stale  # file times: the real clock
    return sorted(rows, key=lambda n: n.get("at", 0)), queued


# -- the queue --------------------------------------------------------------------------


def _beat(path: Path) -> None:
    """The heartbeat: a waiter that stops touching its ticket is passed over."""
    with contextlib.suppress(OSError):
        os.utime(path)


# Every check here is check-then-use on a shared directory, so none is atomic; the contract is
# that a scan never CREATES a ticket and that any misreading (a ghost, a late unlink of a retry's
# new file) lasts one poll at most, because every poll rescans.
def _live(queue: Path, stale_s: float, grace_s: float, mine: str = "") -> list[str]:
    """The names of the tickets still waiting, oldest first.

    A ticket is live when its owner holds the flock on it and beat within ``stale_s``; a
    PARKED one (its owner gave up the wait) while it is younger than ``grace_s``. Anything
    else is dead and removed: a killed waiter's lock went with it."""
    now = CLOCK.wall()
    live = []
    for path in sorted(queue.glob("*.ticket")) + sorted(queue.glob("*.parked")):
        if path.name == mine:
            live.append(path.name)
            continue
        try:
            age = now - path.stat().st_mtime
        except OSError:
            continue
        if path.suffix == ".parked":
            if age <= grace_s:
                live.append(path.name)
                continue
            try:  # expired: gone, unless a retry is taking it back right now
                with fsio.file_lock(path, 0, create=False):
                    with contextlib.suppress(OSError):
                        path.unlink()
            except fsio.LockTimeout:
                if path.exists():  # a retry holds it, and it is still named
                    live.append(path.name)
            except FileNotFoundError:
                pass  # its owner took it or removed it: nothing to count, nothing to recreate
            continue
        try:
            with fsio.file_lock(path, 0, create=False):  # got it: nobody owns this ticket (yet)
                if not path.exists():  # unlinked after the open: the lock is on a dead inode
                    continue
                if age <= NEW_S:  # created, its owner has not locked it yet: it still has its place
                    live.append(path.name)
                else:
                    with contextlib.suppress(OSError):
                        path.unlink()
        except fsio.LockTimeout:
            if age <= stale_s and path.exists():  # held, and still named (not unlinked meanwhile)
                live.append(path.name)
        except FileNotFoundError:
            continue  # the owner left between the stat and the lock: no ghost is made
    return sorted(live, key=lambda n: n.split(".")[0])


def _key(holder: str) -> str:
    holder = re.sub(r"\s*\(pid \d+\)", "", holder)  # a retry is another process: same place
    return "".join(c if c.isalnum() else "_" for c in holder)[:80]


def _parked_for(queue: Path, key: str, grace_s: float) -> Path | None:
    """A ticket this holder parked on an earlier try, still within its grace: its place, to be
    taken back."""
    for path in sorted(queue.glob(f"*-{key}.parked")):
        with contextlib.suppress(OSError):
            if CLOCK.wall() - path.stat().st_mtime <= grace_s:
                return path
    return None


class _Ticket:
    """A waiter's place in the queue: a file it holds the flock on."""

    def __init__(self, queue: Path, holder: str, grace_s: float) -> None:
        queue.mkdir(parents=True, exist_ok=True)
        key = _key(holder)
        self.path = queue / f"{time.time_ns():020d}-{key}.ticket"
        if (parked := _parked_for(queue, key, grace_s)) is not None:
            # A retry takes its place back. The rename is atomic: of two retries racing for
            # one parked ticket exactly one wins it, the other queues anew.
            taken = queue / f"{parked.name[: -len('.parked')]}.ticket"
            try:
                parked.rename(taken)
            except OSError:
                pass
            else:
                self.path = taken
                _beat(taken)  # young: counted live until its owner has locked it
        self.name = self.path.name
        self.park = False
        self._lock = contextlib.ExitStack()

    def __enter__(self) -> _Ticket:
        # A scan that finds an unlocked ticket may hold its lock for an instant (hence LOCK_S);
        # one younger than NEW_S is not removed, so a new owner is never lost in between.
        self._lock.enter_context(fsio.file_lock(self.path, LOCK_S))
        _beat(self.path)
        return self

    def __exit__(self, *exc: object) -> None:
        with contextlib.suppress(OSError):
            if self.park:
                _beat(self.path)  # the grace runs from now, not from the last look
                self.path.rename(self.path.with_suffix(".parked"))
            else:
                self.path.unlink()
        self._lock.close()


def _say_default(text: str) -> None:
    print(text, file=sys.stderr, flush=True)


@contextlib.contextmanager
def hold(
    cfg: Any,
    holder: str,
    directory: Path | str | None = None,
    *,
    say: Callable[[str], None] | None = None,
    wait_s: float | None = None,
) -> Iterator[None]:
    """Hold a full-suite slot for the block, in arrival order; `SuiteBusy` when none frees
    within the wait. ``say`` receives a line whenever the waiter's place in the queue
    changes (default: stderr).

    ``wait_s`` (new, default None) overrides the wait limit `[ci].suite_wait_s` for this
    hold only. The default wait limit counts time IN LINE, not progress:
    once a waiter reaches the head its give-up budget runs fresh from that moment, so the time
    it spent advancing up the line does not bounce it the instant it arrives
    (B-suite-wait-expires-at-head-of-queue). An explicit `wait_s` override stays a hard total
    bound and is not reset at the head."""
    directory = Path(directory) if directory else state_dir()
    limit = max(1, int(cfg.ci.max_concurrent_suites))
    wait = max(0.0, float(wait_s) if wait_s is not None else float(cfg.ci.suite_wait_s))
    stale_s = float(cfg.ci.suite_queue_stale_s)
    grace_s = float(cfg.ci.suite_queue_grace_s)
    say = say or _say_default
    queue = directory / QUEUE
    start = CLOCK.monotonic()
    slots = SLOTS.Slots(directory, NAME)
    said = 0
    with contextlib.ExitStack() as stack:
        ticket = stack.enter_context(_Ticket(queue, holder, grace_s))
        name = ticket.name
        head_since: float | None = None  # monotonic when this waiter FIRST reached the head
        overridden = wait_s is not None  # an explicit `wait_s` bounds the WHOLE queue wait
        while True:
            line = _live(queue, stale_s, grace_s, name)
            position = (line.index(name) + 1) if name in line else len(line) + 1
            if position == 1:
                if head_since is None:
                    head_since = CLOCK.monotonic()
                try:
                    slot = stack.enter_context(slots.hold(limit, 0))
                    break
                except SLOTS.SlotsTimeout:
                    pass
            if position != said:
                said = position
                say(str(SuiteBusy(_holders(directory, limit), CLOCK.monotonic() - start, position)))
            # The default wait limit counts time IN LINE, not progress
            # (B-suite-wait-expires-at-head-of-queue): a waiter behind others gives up after
            # `wait` from when it queued; a waiter at the head gives up after `wait` from when
            # it *arrived* at the head. Otherwise a waiter that spent the whole budget advancing
            # #7 -> #1 is bounced the instant it gets there and the suite is lost with no test
            # run. An explicit `wait_s` override is a hard TOTAL bound (Ba5d5d63f12), so it keeps
            # the old queue-wide anchor. `waited_s` still reports the whole queueing.
            anchor = start if overridden else (head_since if head_since is not None else start)
            if CLOCK.monotonic() - anchor >= wait:
                ticket.park = True
                raise SuiteBusy(_holders(directory, limit), CLOCK.monotonic() - start, position)
            time.sleep(QUEUE_POLL_S)
            _beat(ticket.path)
        # Placed: hand the place on (the ticket goes with this block), then hold.
        note = _note(directory, slot.index)
        now = CLOCK.wall()
        body = {"holder": holder, "pid": os.getpid(), "at": now,
                "at_iso": datetime.fromtimestamp(now).astimezone().strftime("%H:%M:%S")}  # fmt: skip
        with contextlib.suppress(OSError):
            ticket.path.unlink()  # the next in line may try the slots now
        fsio.replace_text(note, json.dumps(body), fsync=False)
        try:
            yield
        finally:
            with contextlib.suppress(OSError):
                note.unlink()
