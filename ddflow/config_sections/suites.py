"""The `[suites]` section: how many full test suites the host runs at once and when it holds one.

Re-exported from `ddflow.config`, which assembles `Config` from every section."""

from __future__ import annotations

from dataclasses import dataclass

from ._docs import declare, int_at_least, knob


def _positive_float(value: object) -> str:
    ok = isinstance(value, int | float) and not isinstance(value, bool) and value > 0
    return "" if ok else "must be a number > 0"


def _poll_seconds(value: object) -> str:
    lo, hi = 0, 600
    ok = isinstance(value, int | float) and not isinstance(value, bool) and lo <= value <= hi
    return "" if ok else "must be a number of seconds from 0 to 600"


@declare("suites")
@dataclass
class SuitesConfig:
    """Admission to the host-wide full-suite lock: a core-derived cap and a load budget."""

    max_concurrent: int = knob(
        0,
        doc="How many full test suites may run at once on this host, counted across every project and agent of the user. 0 (default) derives it from the core count: cores // `cores_per_suite`, at least `[ci].max_concurrent_suites` (which stays the floor), at least 1. A positive value is used as it is. Whatever the cap, a second suite is held back while the host load is over `load_ceiling_per_core`.",
        check=int_at_least(0),
    )
    cores_per_suite: int = knob(
        96,
        doc="Cores one full suite is budgeted, for the derived cap of `max_concurrent`: a 192-core host admits two, a 64-core host one. At least 1.",
        check=int_at_least(1),
    )
    load_ceiling_per_core: float = knob(
        0.35,
        doc="The one-minute load average per core above which a suite waits for another that is already running (six suites at once reached 0.28 to 0.44 per core and starved each other against the per-test timeout). The wait says why and shows the load. A suite that is the only one running is never held back by it: the load is then not from another suite. The wait is queue time: the per-test timeout starts at admission, never earlier. An unreadable load never blocks.",
        check=_positive_float,
    )
    load_poll_s: float = knob(
        2.0,
        doc="Seconds between two readings of the host load by a suite waiting for admission (the queue itself is polled more often). 0 reads it at every poll; at most 600.",
        check=_poll_seconds,
    )
    max_wait_factor: float = knob(
        3.0,
        doc="The aging bound of the suite queue: a waiting run that has waited this many times its own predicted cost goes first, whatever the class or cost of the others, so a long run is never starved by short ones or by higher classes. Must be above 0.",
        check=_positive_float,
    )
    agent_share: float = knob(
        0.5,
        doc="The most of the waiting suite runs one agent may lead, as a share of the queue (rounded up, at least one): the agent's other runs wait behind everybody else's, never dropped. A lone agent keeps its own order. Between 0 and 1.",
        check=lambda v: (
            ""
            if isinstance(v, int | float) and not isinstance(v, bool) and 0 < v <= 1
            else "must be a number above 0 and at most 1"
        ),
    )
    default_cost_s: float = knob(
        600.0,
        doc="The predicted cost, in seconds, of a suite run no measurement of its kind exists for yet: it orders the run among others and sets when it counts as overdue. A finished run teaches its kind's cost (the median of the newest `cost_runs`).",
        check=_positive_float,
    )
    cost_runs: int = knob(
        20,
        doc="How many of the newest measured runs of one kind (its gate and scope) the predicted cost is the median of. At least 1.",
        check=int_at_least(1),
    )
    class_patterns: dict[str, list[str]] = knob(
        factory=lambda: {
            "red_main": ["fix-Bmain-red", "main-red", "red-main"],
            "integrator": ["merge[ _-]?queue", "queue run", "integrator"],
            "phase": ["^P-", "phase[ _-]?(close|suite)"],
            "baseline": ["baseline"],
        },
        doc="How a suite run's class is read from its holder label when its caller names none: regular expressions per class (red_main, integrator, phase, baseline; anything else is an item run). Classes go first in that order: a fix of red main and the merge-queue integrator, then a phase's closing run, then an item's run, then a baseline measurement.",
    )
