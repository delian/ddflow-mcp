"""The `[digest]` section: the operator queue and digest (P-operator-digest, D-OD-Q3).

Re-exported from `ddflow.config`, which assembles `Config` from every section."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from ._docs import declare, int_at_least, knob

LADDER_STEPS = 2
# `config` imports nothing from `core`, so the shape is checked here and the value is read
# by `services/digest/entry.py` with `core.clock.parse_duration`.
_DURATION = re.compile(r"(?:\d+(?:\.\d+)?[smhdw])+|\d+(?:\.\d+)?")


def _is_duration(text: str) -> bool:
    return _DURATION.fullmatch(text.strip().lower().replace(" ", "")) is not None


def _ladder_problem(value: Any) -> str:
    parts = [p.strip() for p in str(value).split(",") if p.strip()]
    if len(parts) != LADDER_STEPS or not all(_is_duration(p) for p in parts):
        return f"{value!r}: give two ages, e.g. '1h, 24h' (needs after the first, urgent after the second)"
    return ""


def _age_problem(value: Any) -> str:
    return "" if _is_duration(str(value)) else f"{value!r} is not a duration such as 6h or 90m"


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
