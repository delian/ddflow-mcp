"""The `[digest]` section: the operator queue and digest (P-operator-digest, D-OD-Q3).

Re-exported from `ddflow.config`, which assembles `Config` from every section."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..core import clock
from ._docs import declare, int_at_least, knob

LADDER_STEPS = 2


def parse_ladder(text: str) -> tuple[float, float]:
    """``"1h, 24h"`` as the two ages (seconds) after which an entry is `needs` and `urgent`."""
    parts = [p.strip() for p in str(text).split(",") if p.strip()]
    if len(parts) != LADDER_STEPS:
        raise ValueError(
            f"{text!r}: give two ages, e.g. '1h, 24h' (needs after the first, urgent after the second)"
        )
    first, second = (clock.parse_duration(p) for p in parts)
    if not 0 <= first <= second:
        raise ValueError(f"{text!r}: the first age must not exceed the second")
    return first, second


def _ladder_problem(value: Any) -> str:
    try:
        parse_ladder(value)
    except (ValueError, TypeError) as exc:
        return str(exc)
    return ""


def _age_problem(value: Any) -> str:
    try:
        seconds = clock.parse_duration(value)
    except (ValueError, TypeError) as exc:
        return str(exc)
    return "" if seconds >= 0 else f"{value!r} is negative: a wait cannot be less than none"


@declare("digest")
@dataclass
class DigestConfig:
    """What waits on the operator: how an unanswered entry escalates and how much is listed."""

    escalate_after: str = knob(
        "1h, 24h",
        doc="Two ages, comma separated. An operator-queue entry is a 'nudge' when it appears, 'needs' once it has waited the first age and 'urgent' once it has waited the second; the age counts from when it first became true, never from the last time anyone looked. An entry whose kind starts higher (see the source table) keeps that floor.",
        check=_ladder_problem,
    )
    escalate_after_red_main: str = knob(
        "6h",
        doc="How long a red main may wait before its queue entry is 'urgent' (it starts at 'needs'). A duration such as 6h or 90m.",
        check=_age_problem,
    )
    text_chars: int = knob(
        400,
        doc="How many characters of a recorded text (an agent's question, a DECIDE task's default line, a failed check) a queue entry quotes before it cuts it. Quoted text has secrets masked first. 0 quotes none.",
        check=int_at_least(0),
    )
    spotcheck_min: int = knob(
        1,
        doc="How many gates passed on refutation, not yet completed, make one spot-check entry in the operator queue. 0 turns that entry off.",
        check=int_at_least(0),
    )
