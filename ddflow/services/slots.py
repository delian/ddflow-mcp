"""Slots: one counting semaphore for every process in a project (D-unify: one local
background layer).

Three limiters exist or are planned, each with its own counting: the adaptive parallelism
the scheduler offers, the review gates that run at once, and the CPU budget of a
background queue. They all ask the same question -- "may I start one more, or must I
wait?" -- of processes that do not share memory (an MCP server, a hook, a CLI call), so
the answer has to live in the filesystem.

A `Slots(directory, name)` is ``limit`` lock files, ``<directory>/<name>.<i>.lock``; a
slot is held by holding an exclusive `flock` on its file (`fsio.file_lock`, the one home of
that call). So a holder that dies -- killed, crashed, the machine going down -- gives its
slot back with the process, with no heartbeat and no stale-owner takeover to get wrong,
and two threads of one process exclude each other too (each opens its own descriptor).

``limit`` is an argument of every call, not a property of the files, so an adaptive limit
that moves between calls just changes how many slots the next caller may try. Lowering it
never evicts a holder: slots above the new limit are finished by whoever has them, and
nobody new takes them -- a semaphore resized the way a pool is.

The clock and the sleep are injectable (`Slots(clock=, sleep=)`), so a test sees a
timeout, a wake-up and a skewed start order without waiting for any of it.
"""

from __future__ import annotations

import contextlib
import os
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

from ..infra import fsio

#: Seconds between two sweeps of the slots while every one is held.
POLL_S = fsio.LOCK_POLL_S


class SlotsTimeout(TimeoutError):
    """Every slot was still held when the timeout ran out."""

    def __init__(self, name: str, limit: int, timeout_s: float) -> None:
        super().__init__(f"all {limit} {name} slot(s) are held after {timeout_s:g}s")
        self.name = name
        self.limit = limit
        self.timeout_s = timeout_s


@dataclass(frozen=True)
class Slot:
    """A slot a caller holds: which one, and how long it waited for it."""

    index: int
    waited_s: float


class Slots:
    def __init__(
        self,
        directory: Path | str,
        name: str,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        poll_s: float = POLL_S,
    ) -> None:
        if not name or "/" in name or name.startswith("."):
            raise ValueError(f"slot name {name!r} must be a plain file-name stem")
        self.directory = Path(directory)
        self.name = name
        self._clock = clock
        self._sleep = sleep
        self._poll_s = poll_s

    def path(self, index: int) -> Path:
        return self.directory / f"{self.name}.{index}.lock"

    @contextlib.contextmanager
    def hold(self, limit: int, timeout_s: float | None = None) -> Iterator[Slot]:
        """Hold one of ``limit`` slots for the block.

        Waits as long as it takes with ``timeout_s=None``; a number gives up with
        `SlotsTimeout` when every slot is still held after that many seconds (0: one
        sweep). ``limit`` below 1 is 1: a limiter that offers nothing would wait forever
        for a slot nobody can hold.
        """
        limit = max(1, int(limit))
        start = self._clock()
        deadline = None if timeout_s is None else start + max(0.0, timeout_s)
        # Callers that all start at slot 0 queue on it while the others sit free; a
        # process-keyed start spreads them, and the sweep still visits every slot.
        first = os.getpid() % limit
        with contextlib.ExitStack() as stack:
            while True:
                for step in range(limit):
                    index = (first + step) % limit
                    try:
                        stack.enter_context(fsio.file_lock(self.path(index), 0))
                    except fsio.LockTimeout:
                        continue
                    slot = Slot(index, round(self._clock() - start, 3))
                    break
                else:
                    if deadline is not None and self._clock() >= deadline:
                        raise SlotsTimeout(self.name, limit, timeout_s or 0.0)
                    self._sleep(self._poll_s)
                    continue
                break
            yield slot

    def try_hold(self, limit: int) -> contextlib.AbstractContextManager[Slot]:
        """`hold` that gives up at once (`SlotsTimeout`) instead of waiting."""
        return self.hold(limit, 0)

    def busy(self, limit: int) -> int:
        """How many of the first ``limit`` slots are held right now, by anyone.

        A probe: it tries each lock for an instant and lets go, so the answer is already
        stale when it is returned. Fit for a status line or a "should I even try" check,
        never for deciding to skip `hold`.
        """
        held = 0
        for index in range(max(1, int(limit))):
            try:
                with fsio.file_lock(self.path(index), 0):
                    pass
            except fsio.LockTimeout:
                held += 1
        return held
