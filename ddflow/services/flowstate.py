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
is skipped (and the file rewritten on the next sample), a clock that went backwards drops
the sample, and a directory that cannot be written makes the limit fall back to its start
value with the reason -- it never raises. The ring's integrity comes from how it is
written -- whole lines appended to an ``O_APPEND`` descriptor, rewrites through a private
temporary file and an atomic rename -- not from the short ``O_EXCL`` lock file, which only
throttles concurrent samplers to one sample per interval.
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
from ..core import clock
from ..core import flowcontrol as FC
from ..core import flowparams as FP
from ..core import flowsignals as FS
from ..core.events import Event
from ..core.model import State
from ..infra import git as G
from ..infra import signals as SIG
from ..infra.fsio import atomic_write
from .configwrite import LOCAL_DIR, ensure_local_dir

#: Where the ring lives, relative to the repository root: under the git-ignored local dir.
RING = LOCAL_DIR / "flow" / "samples.jsonl"
#: How much history the ring keeps.
KEEP_S = 6 * 3600.0
#: A lock file older than this is a crashed sampler's and is taken over.
STALE_LOCK_S = 10.0
#: How long a sampler waits for another one before giving up on this sample.
LOCK_WAIT_S = 2.0

Clock = Callable[[], float]
Events = Sequence[Event] | Callable[[], Sequence[Event]]


@dataclass
class FlowCtx:
    """What sampling and folding need from the loaded project. ``events`` may be a
    callable, read only when a sample is actually due."""

    repo: Path
    cfg: Config
    state: State
    events: Events = field(default_factory=tuple)

    def log_events(self) -> Sequence[Event]:
        return self.events() if callable(self.events) else self.events


@dataclass(frozen=True)
class SampleResult:
    """``written``: a sample was appended. Otherwise ``reason`` says why not; when
    ``unavailable`` is set the ring cannot be used at all (an unwritable directory)."""

    written: bool
    reason: str = ""
    unavailable: bool = False


def ring_path(repo: Path) -> Path:
    return Path(repo) / RING


def _number_or_none(v: object) -> bool:
    return v is None or (isinstance(v, int | float) and not isinstance(v, bool))


def _row_ok(row: object) -> bool:
    """A sample the fold can use: numeric ``at``, a map of numeric-or-None signals and,
    when present, an integer ``in_flight``. Anything else is skipped like a torn line."""
    if not isinstance(row, dict) or not _number_or_none(row.get("at")) or row.get("at") is None:
        return False
    sig = row.get("signals")
    if not isinstance(sig, dict) or not all(_number_or_none(v) for v in sig.values()):
        return False
    flying = row.get("in_flight", 0)
    return isinstance(flying, int) and not isinstance(flying, bool)


def _read(repo: Path) -> tuple[list[dict], bool]:
    """(well-formed samples oldest first, whether the file is clean). Not clean: a line
    was skipped or the last one has no newline -- a torn write -- so the next write
    rewrites the file instead of appending onto the damage."""
    try:
        text = ring_path(repo).read_text("utf-8", errors="replace")
    except OSError:
        return [], True
    rows = []
    lines = text.splitlines()
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if _row_ok(row):
            rows.append(row)
    clean = len(rows) == len(lines) and (not text or text.endswith("\n"))
    return sorted(rows, key=lambda r: r["at"]), clean


def read_ring(repo: Path) -> list[dict]:
    """Every well-formed sample, oldest first. A corrupt or truncated line is skipped."""
    return _read(repo)[0]


def in_flight(state: State, cfg: Config, now: float) -> int:
    """Live leases on items that still exist: what the parallelism limit counts."""
    live = state.active_leases(now, cfg.lease.grace_s)
    return sum(1 for i in live if i in state.items and not state.items[i].removed)


@contextlib.contextmanager
def _lock(path: Path) -> Iterator[bool]:
    """A short lock by ``O_CREAT | O_EXCL`` (no ``fcntl``). Yields False when another
    sampler held it for the whole wait: that sample is skipped.

    It THROTTLES, it does not protect the file: the ring is safe without it, because
    every append is one ``O_APPEND`` write of one line and every rewrite goes through a
    temporary file of the writer's own and an atomic ``os.replace``. So the takeover of
    a crashed sampler's lock (older than ``STALE_LOCK_S``) may race -- two takers can
    both get in, and at worst the ring gains two samples in one interval or a rewrite
    drops one -- but no interleaving tears a line. The token keeps a holder from
    removing a lock that is no longer its own in the ordinary case."""
    lock = path.with_name(path.name + ".lock")
    token = f"{os.getpid()}-{clock.run_stamp()}".encode()
    deadline = time.monotonic() + LOCK_WAIT_S
    fd = None
    while fd is None:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            with contextlib.suppress(OSError):
                if time.time() - lock.stat().st_mtime > STALE_LOCK_S:
                    aside = lock.with_name(f"{lock.name}.stale-{token.decode()}")
                    os.rename(lock, aside)  # one taker wins; the others retry
                    aside.unlink()
                    continue
            if time.monotonic() > deadline:
                yield False
                return
            time.sleep(0.01)
    try:
        _write_all(fd, token)
        os.close(fd)
        fd = None
        yield True
    finally:
        if fd is not None:
            os.close(fd)
        with contextlib.suppress(OSError):
            if lock.read_bytes() == token:
                lock.unlink()


def _write_all(fd: int, data: bytes) -> None:
    """Every byte, however many calls ``os.write`` takes (it may write fewer)."""
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view) :]


def _append_line(path: Path, line: str) -> None:
    """One whole line onto an ``O_APPEND`` descriptor: a line written in one call lands
    whole, after any other appender's. A write cut short (a full disk) needs a second
    call, and another writer's line may land between the two; either way the damaged
    line is seen by the next sample, which rewrites the file -- at most the samples on
    that line are lost, never the ring."""
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        _write_all(fd, line.encode("utf-8"))
    finally:
        os.close(fd)


def _mode(path: Path) -> int:
    """The ring's current mode, so a rewrite keeps it (0o644 when there is none yet).
    Read from the file, never by flipping the umask: that is process-wide, and an MCP
    server has other threads creating files."""
    try:
        return path.stat().st_mode & 0o777
    except OSError:
        return 0o644


def _replace(path: Path, body: str) -> None:
    """Write ``body`` to a temporary file of this call's own (unique per call, even
    between threads), then rename it over ``path`` atomically: a reader sees the old ring
    or the new one, never a mix. It keeps the ring's mode; no fsync: a lost sample is
    taken again."""
    atomic_write(path, body, mode=_mode(path), fsync=False)


def _samples(rows: Sequence[dict]) -> list[FC.Sample]:
    return [FC.Sample(at=float(r["at"]), signals=dict(r["signals"])) for r in rows]


def _history(rows: Sequence[dict]) -> list[tuple[float, int]]:
    return [(float(r["at"]), int(r.get("in_flight", 0) or 0)) for r in rows]


def _log_signals(events: Sequence[Event], now: float) -> dict[str, float | None]:
    """The log-derived rates at ``now`` (cheap scans of the log). Loop findings and
    independent work are the caller's to read at decision time."""
    evs = list(events)
    return {
        "reviewer_latency_ratio": FS.reviewer_latency_ratio(evs, now),
        "gate_failure_rate": FS.gate_failure_rate(evs, now),
        "gate_failure_ratio": FS.gate_failure_ratio(evs, now),
        "merge_failure_rate": FS.merge_failure_rate(evs, now),
    }


def _enabled(cfg: Config, signals: dict[str, float | None]) -> dict[str, float | None]:
    on = set(FP.enabled_signals(cfg))
    return {k: v for k, v in signals.items() if k in on}


def _fold(ctx: FlowCtx, rows: Sequence[dict], now: float, fresh: bool) -> FC.Decision:
    samples = _samples(rows)
    events = ctx.log_events() if fresh and samples else ()
    if events:
        # The log is read at evaluation time: the newest sample carries the rates as
        # they are NOW, so a burst of failures is seen without waiting for a sample.
        last = samples[-1]
        merged = {**last.signals, **_enabled(ctx.cfg, _log_signals(events, now))}
        samples[-1] = FC.Sample(at=last.at, signals=merged)
    return FC.fold_limit(samples, FP.params(ctx.cfg), _history(rows), now)


def _due(rows: Sequence[dict], now: float, interval: int) -> SampleResult | None:
    """None when a sample is due, else why not."""
    if rows and now < rows[-1]["at"]:
        return SampleResult(False, "the clock is earlier than the newest sample; dropped")
    if rows and now - rows[-1]["at"] < interval:
        return SampleResult(False, "not due")
    return None


def sample_if_due(ctx: FlowCtx, source: SIG.SignalSource, clock: Clock = time.time) -> SampleResult:
    """Append one sample when the newest is at least ``signal_interval_s`` old.

    Two calls within the interval write one sample; a clock earlier than the newest
    sample drops this one; an unwritable directory returns ``unavailable``. The slow
    part -- sampling the host and scanning the log -- happens BEFORE the lock, which is
    held only to re-check and write, so a large log never holds other samplers off.
    Never raises.
    """
    try:
        return _sample_if_due(ctx, source, clock)
    except OSError as exc:
        return SampleResult(False, f"the signal ring cannot be written: {exc}", unavailable=True)
    except Exception as exc:  # opportunistic: a sample that fails costs nothing else
        return SampleResult(False, f"sampling failed: {type(exc).__name__}: {exc}")


def _sample_if_due(ctx: FlowCtx, source: SIG.SignalSource, clock: Clock) -> SampleResult:
    now = clock()
    interval = max(1, int(ctx.cfg.schedule.signal_interval_s))
    path = ring_path(ctx.repo)
    rows, _clean = _read(ctx.repo)
    if (why := _due(rows, now, interval)) is not None:
        return why
    raw = source.sample()
    signals = _enabled(
        ctx.cfg, {**SIG.controller_signals(raw), **_log_signals(ctx.log_events(), now)}
    )
    flying = in_flight(ctx.state, ctx.cfg, now)
    binding = flying >= _fold(ctx, rows, now, fresh=False).limit
    reasons = dict(getattr(source, "reasons", {}) or {})
    ensure_local_dir(ctx.repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock(path) as held:
        if not held:
            return SampleResult(False, "another sampler holds the ring")
        rows, clean = _read(ctx.repo)  # again, under the lock
        if (why := _due(rows, now, interval)) is not None:
            return why
        row = {
            "at": now,
            "signals": signals,
            "in_flight": flying,
            "limit_binding": binding,
            "reasons": reasons,
        }
        kept = [r for r in rows if r["at"] >= now - KEEP_S]
        line = json.dumps(row, sort_keys=True) + "\n"
        if clean and len(kept) == len(rows) and path.exists():
            _append_line(path, line)
        else:  # trim, or repair a torn file: rewrite the kept window atomically
            body = "".join(json.dumps(r, sort_keys=True) + "\n" for r in kept) + line
            _replace(path, body)
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


def limit_for(repo: Path, cfg: Config, state: State, events: Events = ()) -> FC.Decision:
    """The limit `plan(parallel=...)` uses, without sampling (the project load already
    took any sample that was due). ``events`` is the log or a callable reading it.

    Never raises, and never silently becomes fixed: when the limit cannot be derived the
    answer is the start value with the reason, so `status` and `brief` still say what is
    in force and why."""
    try:
        return current_limit(FlowCtx(repo=Path(repo), cfg=cfg, state=state, events=events))
    except Exception as exc:  # never let the derived limit stop a command
        from ..core.schedule import LIMIT_UNAVAILABLE

        try:
            start = FP.params(cfg).bounds()[1]  # (floor, start, ceiling)
        except Exception:
            start = max(1, int(cfg.schedule.max_parallel_tasks or 1))
        why = f"the adaptive limit could not be derived ({type(exc).__name__}: {exc})"
        return FC.Decision(start, "start", LIMIT_UNAVAILABLE, why)


def history_notes(cfg: Config, events: Sequence[Event], now: float | None = None) -> list[str]:
    """doctor's notes for the log-derived signals with too little history to be read
    (`flowsignals.history_notes`): none in ``fixed`` mode, where nothing is sampled, and
    none for a signal `[schedule.signals].enabled` switches off."""
    if cfg.schedule.parallel != "auto":
        return []
    at = time.time() if now is None else now
    return FS.history_notes(FS.Signals(**_log_signals(events, at)), FP.enabled_signals(cfg))


def doctor_notes(repo: Path) -> list[str]:
    """Notes, never problems, and writing nothing: a ring that cannot be read or written
    (auto then holds at its start value), and a local directory git does not ignore."""

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
    from ..infra import proc as P

    probe = G.run(repo, "check-ignore", "-q", RING.as_posix(), timeout=P.TIMEOUTS["instant"])
    ignored = 0 if probe.unavailable else probe.code  # cannot tell: say nothing
    if ignored == 1:
        notes.append(
            f"{RING.parent.parent.as_posix()}/ is not ignored by git, so the derived "
            "parallelism state could be committed -- add `local/` to .ddflow/.gitignore"
        )
    return notes
