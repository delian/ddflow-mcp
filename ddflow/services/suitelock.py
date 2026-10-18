"""A host-wide limit on concurrent full test suites (DX-suite-host-lock, first slice).

Two or three CI-parity runs at once took a 192-core host to load 337 (bug B26b5951f2b).
The limit lives in the user's state directory, so every project and every agent of the
user shares it; it is `slots.Slots` (an flock per slot, given back when the holder dies)
plus a note of who holds it, so a refused run can say so. ``[ci].max_concurrent_suites``
(default 1) is how many may run, ``[ci].suite_wait_s`` how long a second one waits."""

from __future__ import annotations

import contextlib
import json
import os
import time
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

from . import slots as SLOTS

NAME = "suites"


class SuiteBusy(RuntimeError):
    """Every suite slot stayed held for the whole wait. Exit 2: could not run."""

    def __init__(self, holder: dict[str, Any], waited_s: float) -> None:
        who = holder.get("holder") or "another run"
        since = holder.get("at_iso") or "an unknown time"
        super().__init__(f"blocked on {who} since {since}")
        self.holder = holder
        self.waited_s = waited_s


def state_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "ddflow" / "suites"


def _note(directory: Path, index: int) -> Path:
    return directory / f"{NAME}.{index}.holder"


def _holders(directory: Path, limit: int) -> dict[str, Any]:
    """The note of the oldest slot's holder (they are only read while the slot is held)."""
    notes = []
    for i in range(limit):
        with contextlib.suppress(OSError, ValueError):
            notes.append(json.loads(_note(directory, i).read_text()))
    return min(notes, key=lambda n: n.get("at", 0), default={})


@contextlib.contextmanager
def hold(cfg: Any, holder: str, directory: Path | str | None = None) -> Iterator[None]:
    """Hold a full-suite slot for the block; `SuiteBusy` when none frees within the wait."""
    directory = Path(directory) if directory else state_dir()
    limit = max(1, int(cfg.ci.max_concurrent_suites))
    wait = max(0.0, float(cfg.ci.suite_wait_s))
    start = time.monotonic()
    try:
        with SLOTS.Slots(directory, NAME).hold(limit, wait) as slot:
            note = _note(directory, slot.index)
            now = time.time()
            body = {"holder": holder, "pid": os.getpid(), "at": now,
                    "at_iso": datetime.fromtimestamp(now).astimezone().strftime("%H:%M:%S")}  # fmt: skip
            note.write_text(json.dumps(body))
            try:
                yield
            finally:
                with contextlib.suppress(OSError):
                    note.unlink()
    except SLOTS.SlotsTimeout:
        raise SuiteBusy(_holders(directory, limit), time.monotonic() - start) from None
