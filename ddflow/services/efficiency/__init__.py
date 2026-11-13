"""Efficiency evaluation: reference workloads and the comparisons that sit on them.

The workload module here judges work by ddflow's own records, never by an agent's
claim; the profile/compare tasks import it. Keep this package free of eager heavy
imports so the workload runner stays cheap enough for CI at no spend.
"""

from . import workloads as work  # noqa: F401
from .workloads import (
    FAIL,
    PASS,
    UNRUN,
    LiveRunRefused,
    Record,
    Report,
    Workload,
    evaluate,
    load_workloads,
    records_from_log,
    run_all,
    run_fake_lane,
    run_live,
)

__all__ = [
    "FAIL",
    "PASS",
    "UNRUN",
    "LiveRunRefused",
    "Record",
    "Report",
    "Workload",
    "evaluate",
    "load_workloads",
    "records_from_log",
    "run_all",
    "run_fake_lane",
    "run_live",
]
