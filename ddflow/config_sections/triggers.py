"""The `[triggers]` section: its dataclass, the values it accepts and its knob docs.

Re-exported from `ddflow.config`, which assembles `Config` from every section."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ._docs import _doc

#: The most fires the cap may allow in an hour: the cap is counted from each trigger's
#: fire tail, and the fold keeps `core.handlers.jobs.TRIGGER_FIRES_KEPT` of them (config
#: imports nothing from the core; tests/test_trigger_cap_knob.py holds the two together).
TRIGGERS_MAX_FIRES_LIMIT = 200


#: The value a bad FILE value of a knob here falls back to: the strictest, not the
#: default (D-enum-fallback-strict), so a typo in the cap stops triggers rather than
#: loosening it. A number never acts outside the clone (D-fallback-no-remote).
TRIGGERS_STRICTEST_NUMBER: dict[str, tuple[int, str]] = {
    "triggers.max_fires_per_hour": (0, "no trigger fires"),
}


def max_fires_problem(v: Any) -> str:
    """ "" for a valid `max_fires_per_hour`, else what it must be."""
    if isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= TRIGGERS_MAX_FIRES_LIMIT:
        return ""
    return f"must be an integer from 0 to {TRIGGERS_MAX_FIRES_LIMIT} (0 = no trigger fires)"


@dataclass
class TriggersConfig:
    """Event triggers (D-sched-triggers-separate): limits across every trigger."""

    #: Fires across EVERY trigger in any rolling hour, at most (D-trigger-cap-knob).
    max_fires_per_hour: int = 10


_doc(
    "triggers",
    "max_fires_per_hour",
    f"How many times triggers may fire, counted across EVERY trigger, in any rolling hour (default 10). A met condition past it is recorded as `trigger.suppressed` with the reason `global_cap` and files nothing. An integer from 0 to {TRIGGERS_MAX_FIRES_LIMIT} (the fire history the log keeps per trigger, which the count reads); 0 stops every trigger at once without disabling any definition.",
)
