"""Compatibility re-export: resource capture moved to `ddflow.adapters.resources` (ADP-migrate-homes).

This shim stays until its import sites import `ddflow.adapters.resources` directly, then it is
deleted.
"""

from ddflow.adapters.resources import *  # noqa: F403  # re-export the whole module
from ddflow.adapters.resources import (  # noqa: F401  # the historical names
    DELTA,
    UNAVAILABLE,
    WAIT4,
    Resources,
    children_snapshot,
    from_rusage,
    maxrss_kb,
    measure,
    metered,
)
