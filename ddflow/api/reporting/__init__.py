"""Read-only questions about the queue and the log. None of these write."""

# The operations live one module per area in this package (B-uni-splits); every name --
# public, private and the modules the single file imported -- is re-exported, so
# `from ddflow.api import reporting as A; A.doctor(...)` keeps working. A REBINDING is not
# shared: each function reads names from its own module, so to replace a helper or a
# constant in a test, patch the module that defines it (`reporting.health._DOCTOR_SWEEP_MAX`),
# not this package.

from __future__ import annotations

import inspect  # noqa: F401
import re  # noqa: F401
import time  # noqa: F401
from pathlib import Path  # noqa: F401
from typing import Any  # noqa: F401

from ...config import Config  # noqa: F401
from ...core import clock  # noqa: F401
from ...core import outcome as O  # noqa: F401
from ...core import progress as PR  # noqa: F401
from ...core.events import version_key  # noqa: F401
from ...core.model import ABANDONED, DONE, fold  # noqa: F401
from ...core.plain import plain  # noqa: F401
from ...core.schedule import (  # noqa: F401
    critical_path,
    is_external,
    stale_package_globs,
    unpickable,
)
from ...core.tier import unknown_tier_notes  # noqa: F401
from ...infra import container as CT  # noqa: F401
from ...infra import signals as SIG  # noqa: F401
from ...infra import worktree as W  # noqa: F401
from ...infra.log import EventLog  # noqa: F401
from ...infra.store import Store  # noqa: F401
from ...infra.worktree import absolutise  # noqa: F401
from ...services import adopt as AD  # noqa: F401
from ...services import cleanup as CL  # noqa: F401
from ...services import configcompat as CC  # noqa: F401
from ...services import embed as EMB  # noqa: F401
from ...services import eventcommit as EC  # noqa: F401
from ...services import external as EX  # noqa: F401
from ...services import flowstate as FL  # noqa: F401
from ...services import gates as G  # noqa: F401
from ...services import launchers as LA  # noqa: F401
from ...services import leases as L  # noqa: F401
from ...services import ledger as LG  # noqa: F401
from ...services import rates as RT  # noqa: F401
from ...services import repairs as RP  # noqa: F401
from ...services import sessions as SS  # noqa: F401
from ...services import sessions as session  # noqa: F401
from ...services import shared_files as SF  # noqa: F401
from ...services import upgrade as UP  # noqa: F401
from ...services import workflow as WF  # noqa: F401
from ...services.adopt import MISSING, NOT_BINDING, rules_status  # noqa: F401
from ...services.completion import verdict  # noqa: F401
from ...services.export import ops as X  # noqa: F401
from ...services.export import select as export_select  # noqa: F401
from ...services.export.query import ExportError  # noqa: F401
from ...services.gates import (
    load_gates,  # noqa: F401
    pipeline_for,  # noqa: F401
)
from ...services.guidance import ruleview as RULEVIEW  # noqa: F401
from ...services.review import load_reviewers  # noqa: F401
from ...views import human  # noqa: F401
from ...views import markdown as render_md  # noqa: F401
from ...views.markdown import may_hold_work  # noqa: F401
from .._base import _load  # noqa: F401
from ..knowledge import _sweep_records, pairs_from  # noqa: F401
from ..lifecycle.planning import plan_for  # noqa: F401
from ..lifecycle.ready import _unknown_phase  # noqa: F401
from ..refs import stale_references  # noqa: F401
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
