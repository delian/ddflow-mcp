"""The `[govern]` section: the session and cost governor's marks, caps, floors and timing.

Read by `core.govern.params_from_config` (D-quotas-governor). Re-exported from
`ddflow.config`, which assembles `Config` from every section."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ._docs import declare, knob

TIER_CHOICES = ("fast", "balanced", "deep")
EFFORT_CHOICES = ("low", "medium", "high")

_LADDER = (
    " Marks are fractions of the allowance (0 to 1); a reading AT a mark steps the ladder up"
    " at once, and it steps down only after falling `hysteresis` under the mark and"
    " `cooldown_s` since the ladder last moved. A mark set out of order is sorted."
)


def _fraction(v: Any) -> str:
    ok = isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= 1
    return "" if ok else "must be a number from 0 to 1"


def _non_negative(v: Any) -> str:
    ok = isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0
    return "" if ok else "must be a number >= 0"


def _mark(default: float, what: str) -> Any:
    return knob(default, doc=what + _LADDER, check=_fraction)


def _tier(default: str, doc: str) -> Any:
    return knob(
        default,
        doc=doc,
        choices=TIER_CHOICES,
        strictest=("fast", "an unknown tier caps work at the cheapest one"),
    )


def _effort(default: str, doc: str) -> Any:
    return knob(
        default,
        doc=doc,
        choices=EFFORT_CHOICES,
        strictest=("low", "an unknown effort caps work at the lowest"),
    )


@declare("govern")
@dataclass
class GovernConfig:
    """The governor: quota and context ladders, tier caps, floors, hysteresis, cooldown."""

    quota_mark_1: float = _mark(
        0.4,
        "Quota fraction (default 0.4) at which new work is capped at `tier_cap_1` and `effort_cap_1`.",
    )
    quota_mark_2: float = _mark(
        0.6,
        "Quota fraction (default 0.6) at which mechanical work is capped at `tier_cap_2` (other work at `tier_cap_2_other`) and effort at `effort_cap_2`.",
    )
    quota_mark_3: float = _mark(
        0.8,
        "Quota fraction (default 0.8) at which the governor pauses and asks (D-quotas: refuse new work, pause items, resume at the reset). 1.0 uses everything.",
    )
    context_mark_1: float = _mark(
        0.6, "Context fraction (default 0.6) at which the session plans a checkpoint."
    )
    context_mark_2: float = _mark(
        0.75, "Context fraction (default 0.75) at which the session writes the handoff."
    )
    context_mark_3: float = _mark(
        0.85,
        "Context fraction (default 0.85) at which the session compacts or restarts at the next safe point.",
    )
    context_mark_4: float = _mark(
        0.95, "Context fraction (default 0.95) at which the session makes an emergency checkpoint."
    )
    hysteresis: float = knob(
        0.05,
        doc="How far under a mark a reading must fall before its ladder steps down (default 0.05), so a reading hovering at a mark does not flap.",
        check=_fraction,
    )
    cooldown_s: float = knob(
        300,
        doc="Seconds a ladder must have been still before it steps down (default 300). Stepping up is never delayed.",
        check=_non_negative,
    )
    tier_cap_1: str = _tier(
        "balanced",
        "The highest model tier new work may use at the first quota (or load) mark: fast | balanced | deep.",
    )
    tier_cap_2: str = _tier(
        "fast",
        "The highest tier MECHANICAL work may use at the second mark: fast | balanced | deep.",
    )
    tier_cap_2_other: str = _tier(
        "balanced",
        "The highest tier work that is not mechanical may use at the second mark (it is held back by `effort_cap_2` instead): fast | balanced | deep.",
    )
    effort_cap_1: str = _effort(
        "medium", "The highest effort at the first mark: low | medium | high."
    )
    effort_cap_2: str = _effort(
        "low", "The highest effort at the second mark: low | medium | high."
    )
    reviewer_floor_tier: str = knob(
        "balanced",
        doc="The tier a reviewer never drops below, whatever the ladder says, so independence and review quality hold: fast | balanced | deep.",
        choices=TIER_CHOICES,
        strictest=("balanced", "an unknown floor keeps reviewers at the balanced tier"),
    )
    effort_floor: str = knob(
        "low",
        doc="The effort no work drops below: low | medium | high.",
        choices=EFFORT_CHOICES,
        strictest=("medium", "an unknown floor keeps medium effort"),
    )
    deep_floor: bool = knob(
        True,
        doc="A task tagged deep (architecture, security) is never downshifted (default true).",
    )
    load_governs: bool = knob(
        True,
        doc="Whether host load (the `[schedule.signals]` marks, read as flowcontrol reads them) also caps the tier: over a signal's high mark counts as the first mark, at its critical mark as the second (default true).",
    )
