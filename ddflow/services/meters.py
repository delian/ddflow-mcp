"""Usage meters: the reader registry, the choice between readings, and the sample log.

Every reader takes a `MeterContext` and returns `Reading`s (`meters_base`). `read_all` runs
them and never raises: a reader that breaks is an UNAVAILABLE reading with the reason, because
a meter is read inside other commands and a missing figure must not become a crash or a zero
(r-no-silent-cost). A new source is one `READERS.register` call (P-governor.context-meter,
B-gap-spend).

`best` picks the figure a caller should act on, and says when it is stale. Reading writes
nothing: only `record` and `sample_if_due` touch the sample log.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterable, Sequence

from ..core import clock
from ..core.registry import Registry
from ..infra.fsio import atomic_write
from ..infra.tomlcfg import locked
from . import meters_base as B
from . import meters_local as L
from . import meters_vendor as V

Reader = Callable[[B.MeterContext], list[B.Reading]]
READERS: Registry[Reader] = Registry("meter", order="registered")
READERS.register(V.read_claude_snapshot, V.SNAPSHOT_SOURCE)
READERS.register(V.read_claude_cache, V.CACHE_SOURCE)
READERS.register(L.read_claude_tokens, L.CLAUDE_SOURCE)
READERS.register(L.read_kilo, L.KILO)
READERS.register(L.read_ddflow_sessions, L.SESSIONS_SOURCE)


def read_all(ctx: B.MeterContext, names: Iterable[str] | None = None) -> list[B.Reading]:
    """Every reading of the readers named (all when None). Never raises."""
    out: list[B.Reading] = []
    for name in READERS if names is None else names:
        try:
            out.extend(READERS[name](ctx))
        except Exception as exc:
            reason = f"{type(exc).__name__}: {exc}"
            out.append(B.unavailable(name, "", "", "", reason, ctx.now))
    return out


def best(
    readings: Sequence[B.Reading], window: str, unit: str, harness: str = ""
) -> B.Reading | None:
    """The reading to act on: the newest fresh figure, else the newest stale one (which says
    so), else an unavailable one with its reason. A reader that broke has no window of its
    own, so its failure answers for every window it might have covered: "it broke" must not
    read as "nothing measures this". None only when nothing covers it and nothing broke."""
    match = [
        r
        for r in readings
        if r.window == window and r.unit == unit and (not harness or r.harness == harness)
    ]
    figures = [r for r in match if r.status == B.OK]
    if figures:
        return max(figures, key=lambda r: (not r.stale, clock.epoch(r.observed_at)))
    broken = [
        r for r in readings if r.status == B.UNAVAILABLE and not (r.window or r.unit or r.harness)
    ]
    return (match or broken or [None])[0]


def _trim(ctx: B.MeterContext) -> None:
    """Keep the newest rows up to half the bound, so a trim is rare, and always the newest row
    whatever its size. Called under the lock."""
    lines = ctx.samples.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    kept: list[str] = []
    size = 0
    for line in reversed(lines):
        size += len(line.encode())
        if kept and size > ctx.samples_max_bytes // 2:
            break
        kept.append(line)
    atomic_write(ctx.samples, "".join(reversed(kept)), mode=0o600)


def _rows_of(ctx: B.MeterContext, readings: Iterable[B.Reading]) -> str:
    return "".join(
        json.dumps({"at": ctx.now, **r.to_dict()}, sort_keys=True) + "\n" for r in readings
    )


def _append(ctx: B.MeterContext, body: str) -> None:
    """Add rows to the log. Called under the lock, because a trim replaces the file: an append
    beside it would land in the file being replaced and be lost."""
    fd = os.open(ctx.samples, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as fh:
        fh.write(body)
    if ctx.samples.stat().st_size > ctx.samples_max_bytes:
        _trim(ctx)


def record(ctx: B.MeterContext, readings: Iterable[B.Reading]) -> None:
    """Append ``readings`` to the sample log."""
    body = _rows_of(ctx, readings)
    if not body:
        return
    ctx.samples.parent.mkdir(parents=True, exist_ok=True)
    with locked(ctx.samples):
        _append(ctx, body)


def _last_at(ctx: B.MeterContext) -> float | None:
    """The time of the log's last row, from its end only."""
    try:
        with ctx.samples.open("rb") as fh:
            fh.seek(max(0, ctx.samples.stat().st_size - ctx.sample_tail_bytes))
            tail = fh.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return None
    for line in reversed(tail):
        try:
            at = json.loads(line).get("at")
        except (ValueError, AttributeError):
            continue
        if isinstance(at, (int, float)):
            return float(at)
    return None


def _due(ctx: B.MeterContext) -> bool:
    last = _last_at(ctx)
    return last is None or not 0 <= ctx.now - last < ctx.sample_interval_s


def sample_if_due(ctx: B.MeterContext, names: Iterable[str] | None = None) -> bool:
    """Read and record once per `[quota].sample_interval_s`. Whether a sample was taken. The
    readers can take seconds, so they run outside the lock and the check is made again under
    it: of two processes that both found it due, the second writes nothing."""
    if not _due(ctx):
        return False
    body = _rows_of(ctx, read_all(ctx, names))
    ctx.samples.parent.mkdir(parents=True, exist_ok=True)
    with locked(ctx.samples):
        if not _due(ctx):
            return False
        _append(ctx, body)
    return True
