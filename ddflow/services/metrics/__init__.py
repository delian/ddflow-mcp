"""Log-derived metrics: ``ddflow metrics`` (summary, forecast, retro, skips, lanes).

One service over one fold of the event log. ``run`` answers every view; ``register`` and
``register_dimension`` let other services add metrics and group-by dimensions; ``grid.sample``
is the shared hourly-grid sampler (the plan at past instants) for lane utilisation and
parallel-headroom views.
"""

from __future__ import annotations

from .grid import sample as sample_grid
from .providers import (
    Context,
    dimension_names,
    register,
    register_dimension,
    unregister,
    unregister_dimension,
)
from .service import VIEWS, Answer, run

__all__ = [
    "VIEWS",
    "Answer",
    "Context",
    "dimension_names",
    "register",
    "register_dimension",
    "run",
    "sample_grid",
    "unregister",
    "unregister_dimension",
]
