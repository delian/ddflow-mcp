"""A host-wide limit on concurrent full test suites (DX-suite-host-lock, first slice).

Two or three CI-parity runs at once took a 192-core host to load 337 (bug B26b5951f2b).
The limit lives in the user's state directory, so every project and every agent of the
user shares it; it is `slots.Slots` (an flock per slot, given back when the holder dies)
plus a note of who holds it, so a refused run can say so. ``limit`` is how many may run
(``[suites].max_concurrent``, else derived from the cores, never under
``[ci].max_concurrent_suites``), ``[ci].suite_wait_s`` how long a second one waits, and a
second suite is also held while the host load is over ``[suites].load_ceiling_per_core``
(``load_block``): the wait is queue time, so the per-test clock starts at admission.

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
import math
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
from ..infra import signals as SIG
from . import slots as SLOTS
from . import suitequeue as SQ

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

    def __init__(
        self,
        holder: dict[str, Any],
        waited_s: float,
        position: int = 0,
        reason: str = "",
        eta: str = "",
    ) -> None:
        who = holder.get("holder") or "another run"
        since = holder.get("at_iso") or "an unknown time"
        pid = holder.get("pid")
        by = f"pid {pid} / {who}" if pid else str(who)
        line = f"blocked on {by} since {since}"
        if position:
            line += f", you are #{position} in the queue"
            if eta:
                line += f", {eta}"
        if reason:
            line += f"; held back: {reason}"
        super().__init__(line)
        self.holder = holder
        self.waited_s = waited_s
        self.position = position
        self.reason = reason


def limit(cfg: Any, cores: int | None = None) -> int:
    """How many suites may run at once: ``[suites].max_concurrent`` when set, else derived from
    the cores (``cores // [suites].cores_per_suite``) and never under ``[ci].max_concurrent_suites``
    (the floor). A config with no ``[suites]`` section keeps the ``[ci]`` value."""
    floor = max(1, int(cfg.ci.max_concurrent_suites))
    if not hasattr(cfg, "suites"):
        return floor
    if int(cfg.suites.max_concurrent) > 0:
        return int(cfg.suites.max_concurrent)
    per = max(1, int(cfg.suites.cores_per_suite))
    return max(floor, (cores or os.cpu_count() or 1) // per)


def load_block(
    cfg: Any, running: int, source: SIG.SignalSource | None = None, cores: int | None = None
) -> str:
    """Why a new suite is held back by the load, "" when it is not. Only while another suite
    runs (a lone suite starts whatever the load: it is not from a suite); an unreadable load
    never blocks."""
    if not hasattr(cfg, "suites") or running < 1:
        return ""
    ceiling = float(cfg.suites.load_ceiling_per_core)
    value = (source or SIG.HostSignals()).sample().get("load")
    if value is None or not math.isfinite(value) or value <= ceiling:
        return ""  # unreadable (absent, NaN, infinite) never blocks
    return (
        f"host load {value:.2f}/core is over the ceiling {ceiling:.2f}/core "
        f"(~{value * (cores or os.cpu_count() or 1):.0f} on {cores or os.cpu_count() or 1} cores) "
        f"with {running} suite(s) running"
    )


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


def _slot_indexes(directory: Path, limit: int) -> list[int]:
    """Every slot a suite may hold right now: this config's ``limit`` slots and any more that
    exist on disk, because the state is shared and another project's config may allow more
    slots than this one (its holders count toward this host's load and cap)."""
    seen = {
        int(m.group(1))
        for p in directory.glob(f"{NAME}.*.lock")
        if (m := re.fullmatch(rf"{NAME}\.(\d+)\.lock", p.name))
    }
    return sorted(seen | set(range(limit)))


def _holders(directory: Path, limit: int) -> dict[str, Any]:
    """The note of the oldest holder among the slots held right now (a crashed holder's note
    outlives it, so a note of a free slot is not read)."""
    notes = []
    for i in _slot_indexes(directory, limit):
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
    for i in _slot_indexes(directory, limit(cfg)):
        if not _held(directory, i):
            continue
        with contextlib.suppress(OSError, ValueError, TypeError, OverflowError):
            note = json.loads(_note(directory, i).read_text())
            if not isinstance(note, dict):
                continue
            if "at" not in note:  # no start time on record: listed, with no age claimed
                rows.append({**note, "slot": i, "age_s": None})
                continue
            began = float(note["at"])  # a non-number raises: the slot is skipped
            rows.append({**note, "at": began, "slot": i, "age_s": max(0, int(at - began))})
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

    def __init__(
        self, queue: Path, holder: str, grace_s: float, note: tuple[str, float, str] | None = None
    ) -> None:
        queue.mkdir(parents=True, exist_ok=True)
        self.note = note
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
        if self.note is not None:
            SQ.write_note(self.path, *self.note)
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


def _note_of(
    cfg: Any, directory: Path, holder: str, klass: str | None, scope: str
) -> tuple[str, float, str] | None:
    """The ticket's note: its class, predicted cost and agent; None without a ``[suites]``
    section (the queue then stays first in, first out)."""
    if not hasattr(cfg, "suites"):
        return None
    kind = klass or SQ.classify(holder, cfg.suites.class_patterns)
    cost = SQ.predicted_cost(directory, scope, keep=int(cfg.suites.cost_runs))
    return (kind, cost or float(cfg.suites.default_cost_s), _key(holder))


def _ordered(cfg: Any, queue: Path, names: list[str]) -> list[SQ.Waiting]:
    """The live tickets in the order the slots are offered (``suitequeue.order``); without a
    ``[suites]`` section, in arrival order."""
    now = CLOCK.wall()
    rows = []
    for n in names:
        note = SQ.read_note(queue / n)
        try:
            waited = max(0.0, now - int(n.split("-", 1)[0]) / 1e9)
        except ValueError:
            waited = float("inf")  # no readable arrival: treated as the oldest, never starved
        rows.append(
            SQ.Waiting(
                n,
                str(note.get("class") or SQ.ITEM),
                float(note.get("cost_s") or 0.0),
                str(note.get("agent") or n.split("-", 1)[-1].rsplit(".", 1)[0]),
                waited,
            )
        )
    if not hasattr(cfg, "suites"):
        return rows
    return SQ.order(
        rows,
        max_wait_factor=float(cfg.suites.max_wait_factor),
        agent_share=float(cfg.suites.agent_share),
        default_cost_s=float(cfg.suites.default_cost_s),
    )


def _default_cost(cfg: Any) -> float:
    return float(cfg.suites.default_cost_s) if hasattr(cfg, "suites") else 600.0


def _eta(
    line: list[SQ.Waiting], name: str, slots_n: int, directory: Path, cfg_default: float = 600.0
) -> str:
    """``estimated wait 14m (3 ahead, predicted 31m)`` for ``name``'s place in ``line``."""
    if not any(w.name == name for w in line):
        return ""
    now = CLOCK.wall()
    free = [0.0] * slots_n  # an idle slot is free now
    busy = []
    for i in _slot_indexes(directory, slots_n):
        if not _held(directory, i):
            continue
        left = float(cfg_default)
        with contextlib.suppress(OSError, ValueError, TypeError, KeyError):
            held = json.loads(_note(directory, i).read_text())
            if held.get("cost_s"):  # no predicted cost: the default, never "about to finish"
                left = float(held["cost_s"])
            left = max(0.0, left - (now - float(held["at"])))
        busy.append(left)
    free = (
        (busy + free)[: max(slots_n, len(busy))]
        if len(busy) >= slots_n
        else [*busy, *free[len(busy) :]]
    )
    est = SQ.estimate(line, name, free_in_s=free, default_cost_s=cfg_default)
    return SQ.say_estimate(est)


class _Admission:
    """What keeps the head of the line out of a slot: the suites already running (every live
    slot, a bigger neighbour's included, counted at every look) and the host load, whose reading
    is remembered for ``[suites].load_poll_s`` while it blocks. With no block the next look
    reads afresh, so a slot freed later is never taken on a stale reading."""

    def __init__(self, cfg: Any, directory: Path, slots_n: int, source: Any, cores: Any) -> None:
        self.cfg, self.directory, self.slots_n = cfg, directory, slots_n
        self.source, self.cores = source, cores
        self.poll_s = float(cfg.suites.load_poll_s) if hasattr(cfg, "suites") else 0.0
        self.running, self.reason = 0, ""
        self._at: float | None = None  # when the load was last read to a block

    def check(self) -> str:
        slots = _slot_indexes(self.directory, self.slots_n)
        self.running = sum(_held(self.directory, i) for i in slots)
        fresh = self._at is not None and CLOCK.monotonic() - self._at < self.poll_s
        if self.running < 1 or not fresh:
            self.reason = load_block(self.cfg, self.running, self.source, self.cores)
            self._at = CLOCK.monotonic() if self.reason else None
        return self.reason

    def reset(self) -> str:
        self.reason, self._at = "", None
        return ""


@contextlib.contextmanager
def hold(
    cfg: Any,
    holder: str,
    directory: Path | str | None = None,
    *,
    say: Callable[[str], None] | None = None,
    wait_s: float | None = None,
    source: SIG.SignalSource | None = None,
    cores: int | None = None,
    klass: str | None = None,
    scope: str = "suite",
) -> Iterator[None]:
    """Hold a full-suite slot for the block, in arrival order; `SuiteBusy` when none frees
    within the wait. ``say`` receives a line whenever the waiter's place in the queue
    changes (default: stderr).

    ``wait_s`` (new, default None) overrides the wait limit `[ci].suite_wait_s` for this
    hold only. The default wait limit counts time IN LINE, not progress:
    once a waiter reaches the head its give-up budget runs fresh from that moment, so the time
    it spent advancing up the line does not bounce it the instant it arrives
    (B-suite-wait-expires-at-head-of-queue). An explicit `wait_s` override stays a hard total
    bound and is not reset at the head.

    ``source`` / ``cores`` (default: this host) are where the load is read: at the head of the
    line a second suite also waits while ``load_block`` says the load is over the ceiling, and
    says why; all of it is queue time, so the caller's clock starts when this yields.

    ``klass`` (default: read from the holder label by ``[suites].class_patterns``) is the
    priority class and ``scope`` names the kind of run, whose measured seconds predict its cost:
    the line says ``you are #N, estimated wait 14m (3 ahead, predicted 31m)`` (SQ.order)."""
    directory = Path(directory) if directory else state_dir()
    slots_n = limit(cfg, cores)
    wait = max(0.0, float(wait_s) if wait_s is not None else float(cfg.ci.suite_wait_s))
    stale_s = float(cfg.ci.suite_queue_stale_s)
    grace_s = float(cfg.ci.suite_queue_grace_s)
    say = say or _say_default
    queue = directory / QUEUE
    start = CLOCK.monotonic()
    slots = SLOTS.Slots(directory, NAME)
    said: tuple[int, str] = (0, "")
    watch = _Admission(cfg, directory, slots_n, source, cores)
    with contextlib.ExitStack() as stack:
        mine = _note_of(cfg, directory, holder, klass, scope)
        ticket = stack.enter_context(_Ticket(queue, holder, grace_s, mine))
        name = ticket.name
        head_since: float | None = None  # monotonic when this waiter FIRST reached the head
        overridden = wait_s is not None  # an explicit `wait_s` bounds the WHOLE queue wait
        while True:
            line = _ordered(cfg, queue, _live(queue, stale_s, grace_s, name))
            names = [w.name for w in line]
            position = (names.index(name) + 1) if name in names else len(line) + 1
            if position == 1:
                if head_since is None:
                    head_since = CLOCK.monotonic()
                reason = watch.check()
                if not reason and watch.running < slots_n:  # a bigger neighbour's holders count
                    try:
                        slot = stack.enter_context(slots.hold(slots_n, 0))
                        break
                    except SLOTS.SlotsTimeout:
                        pass
            else:
                reason = watch.reset()  # not at the head: the line is the reason
            if (position, reason) != said:
                said = (position, reason)
                busy = SuiteBusy(
                    _holders(directory, slots_n),
                    CLOCK.monotonic() - start,
                    position,
                    reason,
                    _eta(line, name, slots_n, directory, _default_cost(cfg)),
                )
                say(str(busy))
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
                raise SuiteBusy(
                    _holders(directory, slots_n),
                    CLOCK.monotonic() - start,
                    position,
                    reason,
                    _eta(line, name, slots_n, directory, _default_cost(cfg)),
                )
            time.sleep(QUEUE_POLL_S)
            _beat(ticket.path)
        # Placed: hand the place on (the ticket goes with this block), then hold.
        note = _note(directory, slot.index)
        now = CLOCK.wall()
        body = {"holder": holder, "pid": os.getpid(), "at": now,
                "at_iso": datetime.fromtimestamp(now).astimezone().strftime("%H:%M:%S")}  # fmt: skip
        if mine is not None:  # without a predicted cost the note says none (unknown, not zero)
            body["cost_s"] = mine[1]
        with contextlib.suppress(OSError):
            ticket.path.unlink()  # the next in line may try the slots now
        fsio.replace_text(note, json.dumps(body), fsync=False)
        ran_from = CLOCK.monotonic()
        try:
            yield
        finally:
            with contextlib.suppress(OSError):
                note.unlink()
            if mine is not None:  # the run's own seconds teach its kind's cost
                SQ.record_cost(
                    directory, scope, CLOCK.monotonic() - ran_from, keep=int(cfg.suites.cost_runs)
                )
