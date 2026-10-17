"""A started command gate run always ends in a recorded outcome (B58842554a7).

`gate run` appends `gate.started`, then runs for minutes. If its process is killed (the
tool timeout, a harness background limit, a SIGKILL) nothing ever records the outcome: the
gate reads as never run, and the output went nowhere. Two defences:

* a started-marker, `.ddflow/runs/<item>/<gate>.running.<pid>.json`, written before the
  command and removed once an outcome is recorded (machine-local, git-ignored with the
  logs), so a LATER call can tell a run that died from one still going;
* a catchable termination (SIGTERM, SIGHUP) is turned into an exception the runner turns
  into an `unavailable` record with the reason, before the process exits.

`dead_runs` is the sweeper's list: the markers whose process is gone on this host.
"""

from __future__ import annotations

import contextlib
import json
import os
import signal
import threading
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from pathlib import Path

from ...core import clock
from ...infra import fsio
from ...infra import hostinfo as H
from ..jobs import alive, proc_start
from .runner import RUNS_DIR


class RunTerminated(KeyboardInterrupt):
    """The run's process was asked to end (SIGTERM, SIGHUP) before its command finished."""

    def __init__(self, signame: str):
        super().__init__(signame)
        self.signame = signame


@dataclass
class Marker:
    item: str
    gate: str
    pid: int
    pid_start: str
    host: str
    at: str
    path: str = ""


def _dir(repo: Path, item: str) -> Path:
    return Path(repo) / ".ddflow" / RUNS_DIR / item


def mark(repo: Path, item: str, gate: str) -> Marker | None:
    """Write this process's started-marker. None where it cannot be written: advisory, the
    run goes on (the sweeper then has nothing to find, as before)."""
    pid = os.getpid()
    m = Marker(item, gate, pid, proc_start(pid), H.hostname(), clock.now_iso())
    try:
        fsio.ensure_ignored_dir(
            Path(repo) / ".ddflow" / RUNS_DIR,
            comment="gate run output logs: machine-local, never committed",
        )
        d = _dir(repo, item)
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{gate}.running.{pid}.json"
        m.path = str(path)
        fsio.replace_text(path, json.dumps(asdict(m)), fsync=False)
    except OSError:
        return None
    return m


def clear(m: Marker | None) -> None:
    if m is not None and m.path:
        Path(m.path).unlink(missing_ok=True)


def _read(path: Path) -> Marker | None:
    try:
        d = json.loads(path.read_text("utf-8"))
        return Marker(**{**d, "path": str(path)})
    except (OSError, ValueError, TypeError):
        return None


def dead_runs(repo: Path, item: str = "") -> list[Marker]:
    """Markers (of ``item``, else every item) whose process no longer exists on this host.
    One on another host, or whose pid now belongs to a different process, is not judged
    from here: an unreadable marker is left alone."""
    root = Path(repo) / ".ddflow" / RUNS_DIR
    dirs = (
        [root / item]
        if item
        else ([p for p in root.iterdir() if p.is_dir()] if root.is_dir() else [])
    )
    out: list[Marker] = []
    for d in dirs:
        for path in sorted(d.glob("*.running.*.json")) if d.is_dir() else []:
            m = _read(path)
            if m is None or not H.same_host(m.host):
                continue
            now = proc_start(m.pid) if m.pid_start else ""
            same = alive(m.pid) and not (m.pid_start and now and now != m.pid_start)
            if m.pid > 0 and not same:
                out.append(m)
    return out


@contextlib.contextmanager
def terminating() -> Iterator[None]:
    """While active, SIGTERM and SIGHUP raise `RunTerminated` in the main thread instead
    of ending the process unrecorded. A no-op off the main thread (signals cannot be set
    there) and where the signals do not exist."""
    names = [n for n in ("SIGTERM", "SIGHUP") if hasattr(signal, n)]
    if threading.current_thread() is not threading.main_thread() or not names:
        yield
        return

    def handler(signum, _frame):
        raise RunTerminated(signal.Signals(signum).name)

    saved = {}
    try:
        for n in names:
            saved[n] = signal.signal(getattr(signal, n), handler)
        yield
    finally:
        for n, old in saved.items():
            with contextlib.suppress(ValueError, OSError):
                signal.signal(getattr(signal, n), old)
