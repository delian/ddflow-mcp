"""Read-only questions about the queue and the log. None of these write."""

# The operations live one module per area in this package (B-uni-splits); the public
# functions and classes are re-exported, so
# `from ddflow.api import reporting as A; A.doctor(...)` keeps working. A REBINDING is not
# shared: each function reads names from its own module, so to replace a helper or a
# constant in a test, patch the module that defines it (`reporting.health._DOCTOR_SWEEP_MAX`),
# not this package.

from __future__ import annotations

from .health import FOLD_PROBLEMS_SHOWN, doctor, fold_problem_notes, recover  # noqa: F401
from .overview import STATUS_LIST_LIMIT, loops, progress, status  # noqa: F401
from .records import addenda, new_reports, record_summary, show  # noqa: F401
from .views import DEFAULT_RENDER_DIR, board, rebuild, render, replay  # noqa: F401
