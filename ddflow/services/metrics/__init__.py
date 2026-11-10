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
from .usage_semantics import (
    CUMULATIVE,
    DELTA,
    FINAL,
    NONE,
    merge_figures,
    resolve_figure,
    semantics_for,
)

__all__ = [
    "CUMULATIVE",
    "DELTA",
    "FINAL",
    "NONE",
    "VIEWS",
    "Answer",
    "Context",
    "dimension_names",
    "merge_figures",
    "register",
    "register_dimension",
    "resolve_figure",
    "run",
    "sample_grid",
    "semantics_for",
    "unregister",
    "unregister_dimension",
]
