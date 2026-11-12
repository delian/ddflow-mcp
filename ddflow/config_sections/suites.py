"""The `[suites]` section: how many full test suites the host runs at once and when it holds one.

Re-exported from `ddflow.config`, which assembles `Config` from every section."""

from __future__ import annotations

from dataclasses import dataclass

from ._docs import declare, int_at_least, knob


def _positive_float(value: object) -> str:
    ok = isinstance(value, int | float) and not isinstance(value, bool) and value > 0
    return "" if ok else "must be a number > 0"


def _non_negative_float(value: object) -> str:
    ok = isinstance(value, int | float) and not isinstance(value, bool) and value >= 0
    return "" if ok else "must be a number >= 0"


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
        doc="Seconds between two readings of the host load by a suite waiting for admission (the queue itself is polled more often). 0 reads it at every poll.",
        check=_non_negative_float,
    )
