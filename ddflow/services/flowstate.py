"""The adaptive limit's local state: a ring of sampled signals, and the limit it implies.

There is no daemon. ``next``, ``brief``, ``claim`` and ``heartbeat`` -- and everything else
that loads the project (``api/_base._load``) -- call :func:`sample_if_due`, which appends at
most one sample per ``schedule.signal_interval_s`` to ``.ddflow/local/flow/samples.jsonl``.
Heartbeats already run every ``lease.heartbeat_s`` on every platform, so a busy project is
sampled steadily and an idle one needs no samples.

The ring is DERIVED and LOCAL: it describes this machine at this minute, lives under the
git-ignored ``.ddflow/local/``, is trimmed to the last six hours, and is never committed.
:func:`current_limit` folds it through the pure controller (``core/flowcontrol``) with the
parameters from ``[schedule]`` (``core/flowparams``), so the same ring always gives the
same limit and a restart loses nothing.

Robust by construction, because it runs inside every command: a corrupt or truncated line
is skipped, a clock that went backwards drops the sample, two agents sampling at once are
serialised by a short ``O_EXCL`` lock file (portable: no ``fcntl``), and a directory that
cannot be written makes the limit fall back to its start value with the reason -- it never
raises.
"""

from __future__ import annotations

import contextlib
import json
import os
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from ..config import Config
from ..core import flowcontrol as FC
from ..core import flowparams as FP
from ..core import flowsignals as FS
from ..core.events import Event
from ..core.model import State
from ..infra import signals as SIG

#: Where the ring lives, relative to the repository root.
RING = Path(".ddflow") / "local" / "flow" / "samples.jsonl"
#: How much history the ring keeps.
KEEP_S = 6 * 3600.0
#: A lock file older than this is a crashed sampler's and is taken over.
STALE_LOCK_S = 10.0
#: How long a sampler waits for another one before giving up on this sample.
LOCK_WAIT_S = 2.0

Clock = Callable[[], float]


@dataclass
class FlowCtx:
    """What sampling and folding need from the loaded project."""

    repo: Path
    cfg: Config
    state: State
    events: Sequence[Event] = field(default_factory=tuple)


@dataclass(frozen=True)
class SampleResult:
    """``written``: a sample was appended. Otherwise ``reason`` says why not; when
    ``unavailable`` is set the ring cannot be used at all (an unwritable directory)."""

    written: bool
    reason: str = ""
    unavailable: bool = False


def ring_path(repo: Path) -> Path:
    return Path(repo) / RING


def read_ring(repo: Path) -> list[dict]:
    """Every well-formed sample, oldest first. A corrupt or truncated line is skipped."""
    try:
        text = ring_path(repo).read_text("utf-8", errors="replace")
    except OSError:
        return []
    rows = []
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if (
            isinstance(row, dict)
            and isinstance(row.get("at"), int | float)
            and isinstance(row.get("signals"), dict)
        ):
            rows.append(row)
    return sorted(rows, key=lambda r: r["at"])


def in_flight(state: State, cfg: Config, now: float) -> int:
    """Live leases on items that still exist: what the parallelism limit counts."""
    live = state.active_leases(now, cfg.lease.grace_s)
    return sum(1 for i in live if i in state.items and not state.items[i].removed)


def _ensure_dir(repo: Path) -> Path:
    """``.ddflow/local/flow``, with ``.ddflow/local`` ignoring itself so nothing under
    it is ever staged, even in a project whose ``.ddflow/.gitignore`` predates it."""
    local = Path(repo) / ".ddflow" / "local"
    d = local / "flow"
    d.mkdir(parents=True, exist_ok=True)
    ignore = local / ".gitignore"
    if not ignore.exists():
        ignore.write_text("*\n", "utf-8")
    return d


@contextlib.contextmanager
def _lock(path: Path, clock: Clock) -> Iterator[bool]:
    """A short exclusive lock by ``O_CREAT | O_EXCL``. Yields False when another sampler
    held it for the whole wait: that sample is simply skipped."""
    lock = path.with_name(path.name + ".lock")
    deadline = time.monotonic() + LOCK_WAIT_S
    fd = None
    while fd is None:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            with contextlib.suppress(OSError):
                if time.time() - lock.stat().st_mtime > STALE_LOCK_S:
                    lock.unlink()
                    continue
            if time.monotonic() > deadline:
                yield False
                return
            time.sleep(0.01)
    try:
        os.write(fd, f"{os.getpid()}\n".encode())
        yield True
    finally:
        os.close(fd)
        with contextlib.suppress(OSError):
            lock.unlink()


def _samples(rows: Sequence[dict]) -> list[FC.Sample]:
    return [FC.Sample(at=float(r["at"]), signals=dict(r["signals"])) for r in rows]


def _history(rows: Sequence[dict]) -> list[tuple[float, int]]:
    return [(float(r["at"]), int(r.get("in_flight", 0) or 0)) for r in rows]


def _log_signals(ctx: FlowCtx, now: float) -> dict[str, float | None]:
    """The log-derived signals at ``now``. Only the rates (cheap scans of the log);
    loop findings and independent work are the caller's to read at decision time."""
    evs = list(ctx.events)
    return {
        "reviewer_latency_ratio": FS.reviewer_latency_ratio(evs, now),
        "gate_failure_rate": FS.gate_failure_rate(evs, now),
        "merge_failure_rate": FS.merge_failure_rate(evs, now),
    }


def _enabled(cfg: Config, signals: dict[str, float | None]) -> dict[str, float | None]:
    on = set(FP.enabled_signals(cfg))
    return {k: v for k, v in signals.items() if k in on}


def _fold(ctx: FlowCtx, rows: Sequence[dict], now: float, fresh: bool) -> FC.Decision:
    samples = _samples(rows)
    if fresh and samples and ctx.events:
        # The log is read at evaluation time: the newest sample carries the rates as
        # they are NOW, so a burst of failures is seen without waiting for a sample.
        last = samples[-1]
        merged = {**last.signals, **_enabled(ctx.cfg, _log_signals(ctx, now))}
        samples[-1] = FC.Sample(at=last.at, signals=merged)
    return FC.fold_limit(samples, FP.params(ctx.cfg), _history(rows), now)


def sample_if_due(ctx: FlowCtx, source: SIG.SignalSource, clock: Clock = time.time) -> SampleResult:
    """Append one sample when the newest is at least ``signal_interval_s`` old.

    Never raises. Two calls within the interval write one sample; a clock earlier than
    the newest sample drops this one; an unwritable directory returns ``unavailable``.
    """
    now = clock()
    interval = max(1, int(ctx.cfg.schedule.signal_interval_s))
    path = ring_path(ctx.repo)
    rows = read_ring(ctx.repo)
    if rows and now - rows[-1]["at"] < interval:
        if now < rows[-1]["at"]:
            return SampleResult(False, "the clock is earlier than the newest sample; dropped")
        return SampleResult(False, "not due")
    try:
        _ensure_dir(ctx.repo)
        with _lock(path, clock) as held:
            if not held:
                return SampleResult(False, "another sampler holds the ring")
            rows = read_ring(ctx.repo)  # again, under the lock
            if rows and now < rows[-1]["at"]:
                return SampleResult(False, "the clock is earlier than the newest sample; dropped")
            if rows and now - rows[-1]["at"] < interval:
                return SampleResult(False, "not due")
            raw = source.sample()
            signals = _enabled(ctx.cfg, {**SIG.controller_signals(raw), **_log_signals(ctx, now)})
            flying = in_flight(ctx.state, ctx.cfg, now)
            decision = _fold(ctx, rows, now, fresh=False)
            row = {
                "at": now,
                "signals": signals,
                "in_flight": flying,
                "limit_binding": flying >= decision.limit,
                "reasons": dict(getattr(source, "reasons", {}) or {}),
            }
            kept = [r for r in rows if r["at"] >= now - KEEP_S]
            line = json.dumps(row, sort_keys=True) + "\n"
            if len(kept) == len(rows) and path.exists():
                with path.open("a", encoding="utf-8") as fh:
                    fh.write(line)
            else:  # trim: rewrite the kept window and the new sample, atomically
                tmp = path.with_name(path.name + ".tmp")
                body = "".join(json.dumps(r, sort_keys=True) + "\n" for r in kept) + line
                tmp.write_text(body, "utf-8")
                os.replace(tmp, path)
    except OSError as exc:
        return SampleResult(False, f"the signal ring cannot be written: {exc}", unavailable=True)
    return SampleResult(True)


def current_limit(
    ctx: FlowCtx, source: SIG.SignalSource | None = None, clock: Clock = time.time
) -> FC.Decision:
    """The parallelism limit in force now. In ``fixed`` mode it is
    ``max_parallel_tasks``; in ``auto`` the ring folded through the controller, after
    taking a sample if one is due and a ``source`` was given."""
    s = ctx.cfg.schedule
    if s.parallel != "auto":
        return FC.Decision(s.max_parallel_tasks, "fixed", "fixed", "schedule.parallel = fixed")
    now = clock()
    if source is not None:
        result = sample_if_due(ctx, source, clock)
        if result.unavailable:
            start = FP.params(ctx.cfg).bounds()[1]
            return FC.Decision(start, "start", FC.NO_SIGNALS, result.reason)
    return _fold(ctx, read_ring(ctx.repo), now, fresh=True)


def doctor_notes(repo: Path) -> list[str]:
    """Notes, never problems, and writing nothing: a ring that cannot be read or written
    (auto then holds at its start value), and a local directory git does not ignore."""
    from ..infra import proc as P

    notes = []
    ring = ring_path(repo)
    nearest = next(
        (p for p in (ring.parent, ring.parent.parent, ring.parent.parent.parent) if p.exists()),
        None,
    )
    if nearest is not None and not os.access(nearest, os.W_OK):
        notes.append(
            f"the adaptive parallelism ring ({RING.as_posix()}) cannot be written ({nearest} "
            "is read-only) -- auto holds at its start value; make .ddflow/local/ writable"
        )
    if ring.exists() and not os.access(ring, os.R_OK):
        notes.append(
            f"the adaptive parallelism ring ({RING.as_posix()}) cannot be read -- auto holds "
            "at its start value"
        )
    try:
        ignored = P.run(
            ["git", "-C", str(repo), "check-ignore", "-q", RING.as_posix()],
            capture_output=True,
            timeout=10,
            check=False,
        ).returncode
    except (OSError, P.TimeoutExpired):
        ignored = 0  # cannot tell; say nothing
    if ignored == 1:
        notes.append(
            f"{RING.parent.parent.as_posix()}/ is not ignored by git, so the derived "
            "parallelism state could be committed -- add `local/` to .ddflow/.gitignore"
        )
    return notes
