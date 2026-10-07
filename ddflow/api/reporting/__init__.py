"""Read-only questions about the queue and the log. None of these write."""

# The operations live one module per area in this package (B-uni-splits); every name --
# public, private and the modules the single file imported -- is re-exported, so
# `from ddflow.api import reporting as A; A.doctor(...)` keeps working. A REBINDING is not
# shared: each function reads names from its own module, so to replace a helper or a
# constant in a test, patch the module that defines it (`reporting.health._DOCTOR_SWEEP_MAX`),
# not this package.

from __future__ import annotations

import re  # noqa: F401
from pathlib import Path  # noqa: F401
from typing import Any  # noqa: F401

from ...config import Config  # noqa: F401
from ...core import outcome as O  # noqa: F401
from ...core.events import version_key  # noqa: F401
from ...core.model import ABANDONED, DONE, fold  # noqa: F401
from ...core.plain import plain  # noqa: F401
from ...core.schedule import stale_package_globs  # noqa: F401
from ...core.tier import unknown_tier_notes  # noqa: F401
from ...infra import worktree as W  # noqa: F401
from ...infra.log import EventLog  # noqa: F401
from ...views.markdown import may_hold_work  # noqa: F401
from .._base import _load  # noqa: F401
from .health import (  # noqa: F401
    _DOCTOR_PAIR_CAP,
    _DOCTOR_SWEEP_MAX,
    _SHARDS_NAMED,
    _SHARDS_SHOWN,
    _UNTITLED_SHOWN,
    FOLD_PROBLEMS_SHOWN,
    _dependency_findings,
    _driver_drift_notes,
    _dupe_note,
    _export_target_notes,
    _finished_phase_remedy,
    _launcher_findings,
    _loose_shards,
    _only_ids,
    _orphan_notes,
    _primary_mid_merge,
    _stale_glob_notes,
    _unknown_author_notes,
    _untitled,
    doctor,
    fold_problem_notes,
    recover,
)
from .overview import (  # noqa: F401
    STATUS_LIST_LIMIT,
    _bound,
    loops,
    progress,
    status,
)
from .records import (  # noqa: F401
    _TEXT_CAP,
    _epoch,
    _show_bug,
    addenda,
    new_reports,
    record_summary,
    show,
)
from .views import (  # noqa: F401
    _RENDERABLE,
    DEFAULT_RENDER_DIR,
    board,
    rebuild,
    render,
    replay,
)
