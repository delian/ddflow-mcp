"""What a usage meter hands back, and what it reads from (D-quotas-governor (3)).

A `Reading` is one figure from one source for one window. It cannot say "no figure" with a
number: `status` ok needs a real number and unavailable forbids one, so a reader that failed
has no way to report zero (r-no-silent-cost). A measured zero is `ok` with `0.0`, and only a
source that really measured it may say so.

The figures are the user's, not the project's: several projects share one account, so the
snapshot and the sample log live in the user's state directory (D-quotas-governor (6)).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from functools import cache
from pathlib import Path
from typing import Any

from ..adapters import env as ENV
from ..adapters import toml as TOML
from ..config import Config
from ..core import clock
from ..infra import paths
from .quota import PERCENT_LIMIT, WINDOWS

OK = "ok"
UNAVAILABLE = "unavailable"
#: The file names used when `[quota].snapshot_path` and `samples_path` are empty.
SNAPSHOT_NAME = "vendor-usage.json"
SAMPLES_NAME = "usage-samples.jsonl"


def is_number(value: object) -> bool:
    """A finite real number. An int too large for a float is not one: nothing here measures it."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


@dataclass(frozen=True)
class Reading:
    source: str
    harness: str
    window: str
    unit: str
    status: str
    value: float | None
    #: When the figure was observed: the vendor's fetch time, else the time of this read.
    observed_at: str
    stale: bool
    resets_at: str = ""
    #: `quota.account_tag` of the account, never the raw id; "" when the source does not say.
    account: str = ""
    reason: str = ""
    #: Turns the source held no usable figure for: the value is then a lower bound.
    unmetered: int = 0
    detail: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status == OK and not is_number(self.value):
            raise ValueError(f"{self.source}: an ok reading needs a number, got {self.value!r}")
        if self.status == UNAVAILABLE and self.value is not None:
            raise ValueError(f"{self.source}: an unavailable reading has never a value")
        if self.status == UNAVAILABLE and not self.reason:
            raise ValueError(f"{self.source}: an unavailable reading needs a reason")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MeterContext:
    """Everything a reader needs, so a reader touches no global and a test no real home."""

    now: float
    stale_after_s: float
    claude_state: Path
    claude_projects: Path
    kilo_db: Path
    snapshot: Path
    samples: Path
    #: Window name -> the epoch it starts at. Rolling by default; a caller with a fixed
    #: window hands in its own start.
    windows: dict[str, float]
    token_fields: tuple[str, ...]
    transcript_max_bytes: int
    kilo_timeout_s: float
    samples_max_bytes: int
    sample_interval_s: float
    sample_tail_bytes: int
    #: The project's events, for the session counter; None when the caller has none.
    events: Sequence[Any] | None = None


def iso_of(t: float) -> str:
    return clock.iso_at(t)


def unavailable(
    source: str, harness: str, window: str, unit: str, reason: str, now: float, unmetered: int = 0
) -> Reading:
    """A reading with no figure. `observed_at` says when the attempt was made."""
    return Reading(
        source,
        harness,
        window,
        unit,
        UNAVAILABLE,
        None,
        iso_of(now),
        False,
        reason=reason,
        unmetered=unmetered,
    )


def percent(raw: object, scale: float) -> float | None:
    """``raw`` as a percent from 0 to 100, or None when it is not one. ``scale`` is the source's
    own: 1 for a source already in percent, 100 for one reporting 0 to 1. Out of range is
    refused, never clamped: 53 from a 0-to-1 source means the scale is wrong, not 100%."""
    if not is_number(raw):
        return None
    value = float(raw) * scale
    return value if 0 <= value <= PERCENT_LIMIT else None


def is_stale(ctx: MeterContext, observed: float, resets: float | None) -> bool:
    """Too old, from a clock that disagrees with ours, or from before its window reset."""
    if abs(ctx.now - observed) > ctx.stale_after_s:
        return True
    return resets is not None and resets <= ctx.now


@cache
def sources() -> dict[str, Any]:
    """`templates/meter_sources.toml`: which harness each meter reads, and its default paths."""
    return TOML.TomlLibIo().read(paths.templates_dir() / "meter_sources.toml")


def state_dir() -> Path:
    """The user's state directory for ddflow: `$XDG_STATE_HOME/ddflow`, else the default."""
    base = ENV.DEFAULT.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "ddflow"


def _windows(names: Sequence[str], now: float) -> dict[str, float]:
    starts: dict[str, float] = {}
    for name in names:
        length = WINDOWS[name]
        starts[name] = 0.0 if length is None else now - length
    return starts


def context(cfg: Config, *, now: float | None = None, events: Sequence[Any] | None = None):
    """The `MeterContext` of ``cfg``, at ``now`` (the clock when None)."""
    q = cfg.quota
    data = sources()
    at = clock.now_utc().timestamp() if now is None else now
    return MeterContext(
        now=at,
        stale_after_s=q.stale_after_s,
        claude_state=Path(q.claude_state or data["vendor"]["state"]).expanduser(),
        claude_projects=Path(q.claude_projects or data["transcripts"]["root"]).expanduser(),
        kilo_db=Path(q.kilo_db or data["store"]["path"]).expanduser(),
        snapshot=Path(q.snapshot_path).expanduser()
        if q.snapshot_path
        else state_dir() / SNAPSHOT_NAME,
        samples=Path(q.samples_path).expanduser() if q.samples_path else state_dir() / SAMPLES_NAME,
        windows=_windows(q.token_windows, at),
        token_fields=tuple(q.token_fields),
        transcript_max_bytes=q.transcript_max_bytes,
        kilo_timeout_s=q.kilo_timeout_s,
        samples_max_bytes=q.samples_max_bytes,
        sample_interval_s=q.sample_interval_s,
        sample_tail_bytes=q.sample_tail_bytes,
        events=events,
    )
