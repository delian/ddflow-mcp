"""Ticks: the one place ddflow does small periodic work without a daemon.

D-unify, one local background layer. ddflow has no resident process, so anything that
should happen "every so often" -- the adaptive-parallelism sample, a schedule coming due,
a release check, a quota meter, a digest-drift check -- rides on a command that is running
anyway. Each used to wire itself into whichever command it thought of; here each registers
a `Tick` (a name, how often, how long it may take, what to call) and `run_due` -- called
from ONE place, `api._base._load`, which `next`, `brief`, `claim` and `heartbeat` all pass
through -- runs the ones that are due.

What the primitive promises, and the tests pin:

- **Due, per machine.** A tick runs when ``every_s`` seconds have passed since it last ran
  here (`.ddflow/local/ticks.json`, git-ignored). The run is CLAIMED under a short lock
  before it starts and recorded after, so two commands starting together do not both run
  it, and the lock is not held while the tick runs. A tick that dies mid-run is not
  retried before its next due time: this is opportunistic work, not a job queue.
- **Budgeted.** Each tick has a time budget and the whole pass has one. A tick runs in a
  worker thread; one that overruns is CUT -- the command goes on without it -- and the
  overrun is recorded (`status: cut`, with what it took) where `status()` and a later
  doctor line can show it. A tick not reached before the pass budget is spent is
  DEFERRED (not marked as run), so the next command picks it up.
- **Never in the way.** `run_due` does not raise: a tick that fails is recorded as failed,
  a state directory that cannot be written is reported as unavailable, and the command
  that called goes on either way.
- **Testable.** The wall clock and the monotonic clock are arguments.
"""

from __future__ import annotations

import contextlib
import dataclasses
import importlib
import json
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..infra import fsio

STATE = "ticks.json"
LOCK = "ticks.lock"
FORMAT = 1

#: Seconds the whole pass may spend running ticks before the rest are deferred.
PASS_BUDGET_S = 15.0
#: A claim is honoured for this many budgets of the claimed tick before it is taken for
#: a command that died mid-run.
CLAIM_WINDOWS = 2
#: Seconds a state-file lock is waited for: it is held only to read, claim and write.
LOCK_WAIT_S = 1.0

RAN = "ran"
RUNNING = "running"
CUT = "cut"
FAILED = "failed"
DEFERRED = "deferred"
LOCKED = "locked"
UNAVAILABLE = "unavailable"
STATUSES = (RAN, CUT, FAILED, DEFERRED, LOCKED, UNAVAILABLE)


class TickUnavailable(Exception):
    """Raised by a tick that could not do its work at all (an unwritable directory, a tool
    that is absent): recorded as `unavailable`, never as a run that went fine."""


@dataclass
class TickCtx:
    """What a tick is given: the project, and how much of its budget is left."""

    repo: Path
    cfg: Any
    state: Any
    #: The event log, read only when a tick asks for it (`log_events()`).
    events: Callable[[], list[Any]] = lambda: []
    deadline: float | None = None
    mono: Callable[[], float] = time.monotonic

    def log_events(self) -> list[Any]:
        return self.events()

    def remaining(self) -> float:
        """Seconds of this tick's budget left (inf when it has none)."""
        return float("inf") if self.deadline is None else self.deadline - self.mono()

    def expired(self) -> bool:
        """A tick that loops checks this between steps and stops cleanly when True."""
        return self.remaining() <= 0


@dataclass(frozen=True)
class Tick:
    name: str
    #: Minimum seconds between two runs on this machine (0: whenever the pass runs).
    every_s: float
    #: Seconds one run may take before it is cut.
    budget_s: float
    #: The callable, or "package.module:function" resolved on first use (so registering
    #: costs no import). It returns None or a one-line note.
    target: Callable[[TickCtx], str | None] | str
    #: Whether the tick applies to this project at all; None: always.
    enabled: Callable[[TickCtx], bool] | None = None

    def __post_init__(self) -> None:
        if not self.name or "\n" in self.name:
            raise ValueError("a tick needs a one-line name")
        if self.every_s < 0 or self.budget_s <= 0:
            raise ValueError(f"tick {self.name!r}: every_s must be >= 0 and budget_s > 0")

    def resolve(self) -> Callable[[TickCtx], str | None]:
        if callable(self.target):
            return self.target
        module, _, attr = self.target.partition(":")
        return getattr(importlib.import_module(module), attr)


@dataclass(frozen=True)
class TickResult:
    name: str
    status: str
    detail: str = ""
    took_s: float = 0.0

    def data(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
            "took_s": round(self.took_s, 3),
        }


REGISTRY: dict[str, Tick] = {}


def register(tick: Tick) -> Tick:
    """Add ``tick`` to the registry. Registering the same definition again is a no-op;
    another definition under a taken name is an error (two owners of one name would share
    its last-run time and starve each other)."""
    have = REGISTRY.get(tick.name)
    if have is not None and have != tick:
        raise ValueError(f"tick {tick.name!r} is already registered with another definition")
    REGISTRY[tick.name] = tick
    return tick


def unregister(name: str) -> None:
    REGISTRY.pop(name, None)


def registered() -> list[Tick]:
    return [REGISTRY[n] for n in sorted(REGISTRY)]


# -- state ----------------------------------------------------------------------------------


def _dir(repo: Path | str) -> Path:
    return Path(repo) / ".ddflow" / "local"


def _read(path: Path) -> dict[str, dict[str, Any]]:
    data = fsio.read_json(path)
    if isinstance(data, fsio.Unreadable) or not isinstance(data, dict):
        return {}
    ticks = data.get("ticks")
    if not isinstance(ticks, dict):
        return {}
    return {k: v for k, v in ticks.items() if isinstance(k, str) and isinstance(v, dict)}


def _write(path: Path, ticks: dict[str, dict[str, Any]]) -> None:
    fsio.atomic_write(path, json.dumps({"format": FORMAT, "ticks": ticks}, indent=1) + "\n")


def status(repo: Path | str) -> list[dict[str, Any]]:
    """What each registered tick last did on this machine: for a doctor line or a person.
    A tick that has never run reports ``status: never``; one with no interval (`every_s == 0`:
    asked on every pass, recorded only when it fails or is cut, and when a success follows a
    recorded failure) reports ``every-pass`` until one of those happens."""
    rows = _read(_dir(repo) / STATE)
    out = []
    for t in registered():
        last = rows.get(t.name, {})
        out.append(
            {
                "name": t.name,
                "every_s": t.every_s,
                "budget_s": t.budget_s,
                "last_at": last.get("last_at"),
                "status": last.get("status", "every-pass" if t.every_s == 0 else "never"),
                "detail": last.get("detail", ""),
                "took_s": last.get("took_s", 0.0),
                "cut": int(last.get("cut", 0)),
            }
        )
    return out


# -- the pass -------------------------------------------------------------------------------


def _due(t: Tick, row: dict[str, Any], now: float) -> bool:
    last = row.get("last_at")
    if not isinstance(last, int | float):
        return True
    if now < last:
        return False  # the clock went back: wait until it catches up, never run twice
    if row.get("status") == RUNNING and now - last < CLAIM_WINDOWS * t.budget_s:
        # Claimed by a command that has not finished -- a tick that outruns its own interval
        # is still ours until its budget (and a margin) has passed, not due again.
        return False
    return now - last >= t.every_s


def _claim(path: Path, due: list[Tick], now: float) -> list[Tick]:
    """Mark each due tick as started and return those THIS process won. Under the lock, so
    two commands that both saw a tick due do not both run it."""
    won: list[Tick] = []
    with fsio.file_lock(path.with_name(LOCK), LOCK_WAIT_S):
        rows = _read(path)
        for t in due:
            if _due(t, rows.get(t.name, {}), now):
                rows[t.name] = {**rows.get(t.name, {}), "last_at": now, "status": RUNNING}
                won.append(t)
        if won:
            _write(path, rows)
    return won


def _record(path: Path, t: Tick, result: TickResult, *, live_at: float | None = None) -> None:
    """Write ``result`` as ``t``'s last outcome. ``live_at`` (the time of this pass): leave a
    row another command is running RIGHT NOW alone -- a note about a tick must not end its
    claim -- but not one whose claim has outlived its window (a command that died)."""
    with fsio.file_lock(path.with_name(LOCK), LOCK_WAIT_S):
        rows = _read(path)
        row = rows.get(t.name, {})
        if live_at is not None and row.get("status") == RUNNING:
            last = row.get("last_at")
            if isinstance(last, int | float) and live_at - last < CLAIM_WINDOWS * t.budget_s:
                return
        rows[t.name] = {
            **row,
            "status": result.status,
            "detail": result.detail,
            "took_s": round(result.took_s, 3),
            "cut": int(row.get("cut", 0)) + (1 if result.status == CUT else 0),
        }
        _write(path, rows)


@dataclass
class _Run:
    note: str | None = None
    error: BaseException | None = None
    done: threading.Event = field(default_factory=threading.Event)


def _call(t: Tick, ctx: TickCtx, wait_s: float) -> tuple[str, str]:
    """Run ``t`` in a worker thread for at most ``wait_s``: (status, detail)."""
    run = _Run()

    def work() -> None:
        try:
            run.note = t.resolve()(ctx)
        except BaseException as exc:  # a tick must not take the command down with it
            run.error = exc
        finally:
            run.done.set()

    worker = threading.Thread(target=work, name=f"tick:{t.name}", daemon=True)
    try:
        worker.start()
    except RuntimeError as exc:  # no thread to be had on this host
        return FAILED, f"RuntimeError: {exc}"
    if not run.done.wait(max(0.0, wait_s)):
        return CUT, f"still running after {wait_s:g}s; the command went on without it"
    if isinstance(run.error, TickUnavailable):
        return UNAVAILABLE, str(run.error)
    if run.error is not None:
        return FAILED, f"{type(run.error).__name__}: {run.error}"
    return RAN, run.note or ""


def run_due(
    ctx: TickCtx,
    *,
    ticks: Iterable[Tick] | None = None,
    pass_budget_s: float = PASS_BUDGET_S,
    clock: Callable[[], float] = time.time,
    mono: Callable[[], float] = time.monotonic,
) -> list[TickResult]:
    """Run every registered (or given) tick that is due. Never raises.

    Returns one `TickResult` per tick that was attempted or deferred; ticks that were not
    due, or do not apply to this project, are left out."""
    ctx.mono = mono
    try:
        return _run_due(ctx, list(registered() if ticks is None else ticks), pass_budget_s, clock)
    except fsio.LockTimeout as exc:
        return [TickResult("*", LOCKED, f"another command holds the tick state: {exc}")]
    except OSError as exc:
        return [TickResult("*", UNAVAILABLE, f"the tick state cannot be written: {exc}")]
    except Exception as exc:  # opportunistic: nothing here costs the caller anything
        return [TickResult("*", FAILED, f"{type(exc).__name__}: {exc}")]


def _applies(t: Tick, ctx: TickCtx) -> tuple[bool, str]:
    """`(applies, why not could be told)`: a predicate that raises is a tick that could not
    be judged, which is not the same as one that does not apply."""
    if t.enabled is None:
        return True, ""
    try:
        return bool(t.enabled(ctx)), ""
    except Exception as exc:
        return False, f"its enabled check failed: {type(exc).__name__}: {exc}"


def _split_due(
    ticks: list[Tick], ctx: TickCtx, rows: dict[str, dict[str, Any]], now: float
) -> tuple[list[Tick], list[TickResult]]:
    """`(ticks due, results for ticks that could not be judged)`."""
    due: list[Tick] = []
    unjudged: list[TickResult] = []
    for t in ticks:
        applies, why = _applies(t, ctx)
        if why:
            unjudged.append(TickResult(t.name, FAILED, why))
        elif applies and _due(t, rows.get(t.name, {}), now):
            due.append(t)
    return due, unjudged


def _run_one(t: Tick, ctx: TickCtx, path: Path, budget_left: float, prior: str = "") -> TickResult:
    """Run ``t`` inside its budget (and what is left of the pass's) and record the outcome."""
    budget = min(t.budget_s, budget_left)
    begun = ctx.mono()
    # Its own context: a tick that was cut keeps seeing ITS deadline (expired) while the
    # pass moves on to the next tick's.
    tctx = dataclasses.replace(ctx, deadline=begun + budget)
    status_, detail = _call(t, tctx, budget)
    result = TickResult(t.name, status_, detail, ctx.mono() - begun)
    # A tick with `every_s == 0` has its own throttle (it is asked on every pass), so it is
    # recorded only when it fails or is cut -- the state file is not rewritten on every
    # command -- which a doctor line must be able to show,
    # ... and when a success follows a recorded failure, so a healthy tick is not shown broken.
    if t.every_s > 0 or status_ != RAN or prior in (FAILED, CUT, UNAVAILABLE):
        with contextlib.suppress(OSError, fsio.LockTimeout):
            _record(path, t, result)
    return result


def _run_due(
    ctx: TickCtx, ticks: list[Tick], pass_budget_s: float, clock: Callable[[], float]
) -> list[TickResult]:
    if not (Path(ctx.repo) / ".ddflow").is_dir():
        return []
    path = _dir(ctx.repo) / STATE
    now = clock()
    rows = _read(path)
    due, results = _split_due(ticks, ctx, rows, now)
    if not due and not results:
        return []
    fsio.ensure_ignored_dir(_dir(ctx.repo))  # only now that something will be written
    by_name = {t.name: t for t in ticks}
    for r in results:  # a tick that could not be judged is shown, once, not dropped
        prior = rows.get(r.name, {})
        if (prior.get("status"), prior.get("detail")) != (r.status, r.detail):
            with contextlib.suppress(OSError, fsio.LockTimeout):
                _record(path, by_name[r.name], r, live_at=now)
    started = ctx.mono()
    stateful_due = [t for t in due if t.every_s > 0]
    claimed = {t.name for t in _claim(path, stateful_due, now)} if stateful_due else set()
    pending = set(claimed)  # claimed and not yet run: handed back if this pass is cut short
    try:
        for t in due:
            if t.every_s > 0 and t.name not in claimed:
                continue  # another command won it
            left = pass_budget_s - (ctx.mono() - started)
            if left <= 0:
                # Not run, so not recorded as run: handed back below, the next command takes it.
                results.append(TickResult(t.name, DEFERRED, "the pass budget was spent"))
                continue
            pending.discard(t.name)
            results.append(_run_one(t, ctx, path, left, rows.get(t.name, {}).get("status", "")))
    finally:
        for name in sorted(pending):  # an abort (KeyboardInterrupt, ...) must not strand a claim
            with contextlib.suppress(OSError, fsio.LockTimeout):
                _release(path, by_name[name], now)
    return results


def _release(path: Path, t: Tick, claimed_at: float) -> None:
    """Undo OUR claim of a tick that was not run: it is due again. A row another command has
    claimed or recorded since (its `last_at` is no longer ours) is left alone."""
    with fsio.file_lock(path.with_name(LOCK), LOCK_WAIT_S):
        rows = _read(path)
        row = rows.get(t.name, {})
        if row.get("last_at") != claimed_at or row.get("status") != RUNNING:
            return
        row.pop("last_at", None)
        row["status"] = DEFERRED
        rows[t.name] = row
        _write(path, rows)


# -- the ticks ddflow ships -------------------------------------------------------------------


def _flow_enabled(ctx: TickCtx) -> bool:
    return getattr(getattr(ctx.cfg, "schedule", None), "parallel", "") == "auto"


def flow_sample(ctx: TickCtx) -> str | None:
    """The adaptive-parallelism sample (B-af-sampler), itself throttled to one per
    `schedule.signal_interval_s`; the tick only decides that it may run. The modules are
    imported here, by name, so registering this tick costs no import on a command that
    never samples."""
    signals = importlib.import_module("ddflow.infra.signals")
    flowstate = importlib.import_module("ddflow.services.flowstate")
    fctx = flowstate.FlowCtx(repo=Path(ctx.repo), cfg=ctx.cfg, state=ctx.state, events=ctx.events)
    result = flowstate.sample_if_due(fctx, signals.HostSignals(ctx.repo))
    if result.unavailable:
        raise TickUnavailable(result.reason)
    return None if result.written else (result.reason or None)


register(
    Tick(
        "flow.sample",
        every_s=0,
        budget_s=10.0,
        target=flow_sample,
        enabled=_flow_enabled,
    )
)
