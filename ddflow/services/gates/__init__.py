"""Gates — the quality pipeline every task and every phase passes through.

A gate is one checkpoint with one outcome. Two kinds exist and the difference matters:

* a **command gate** has a shell command; ddflow runs it and the exit code decides;
* an **agent gate** has none, because the work is judgement an LLM does (research,
  a rubber-duck review, a bug hunt). ddflow cannot perform it, so it *demands the
  evidence* and records the agent's answer.

Agent gates are where a workflow usually rots, because "I reviewed it" costs nothing
to say. Three rules push back:

1. **UNAVAILABLE is an outcome, not a synonym for pass.** A reviewer whose endpoint was
   down did not approve anything. It gets its own outcome, its own exit code, and it
   shows up in the completion report as a gap.
2. **Evidence is required for the gates listed in ``gates.evidence_required``.** A
   record with no command, no exit code and no output digest is rejected at the API
   boundary, so a gate cannot be passed by assertion.
3. **A different-family reviewer is checked, not trusted.** Same-family reviewers share
   the author's blind spots, so their agreement is not independent evidence. The runner
   knows which family produced each review and reports when they all match the author.

Exit-code vocabulary, uniform across every ddflow command:
``0`` healthy · ``1`` real failure · ``2`` could not run / nothing to do · ``3``
coordination refused. ``2`` is never collapsed into ``0``; "no data" is reported, never
treated as "no problem".
"""

# The gate machinery lives one module per area in this package (B-uni-splits); every name --
# public, private and the modules the single file imported, except the two below -- is
# re-exported, so `from ddflow.services import gates as G; G.run_command_gate(...)` keeps
# working. A REBINDING is not shared: each function reads names from its own module, so to
# replace a helper or a constant in a test, patch the module that defines it
# (`gates.evidence.MAX_UNTRACKED_HASHED`), not this package.

# `subprocess` and `tempfile` are imported by the areas that use them and NOT re-exported
# here: each is confined to its one home by .importlinter, and the package importing them
# again would be one more violation (nothing reads `gates.subprocess`). Digests go
# through `core.digest`, re-exported like any other name an area binds.

from __future__ import annotations

import getpass  # noqa: F401
import json  # noqa: F401
import os  # noqa: F401
import re  # noqa: F401
import shlex  # noqa: F401
import shutil  # noqa: F401
import socket  # noqa: F401
import sys  # noqa: F401
import time  # noqa: F401
import tomllib  # noqa: F401
from collections.abc import Callable, Iterable, Mapping  # noqa: F401
from dataclasses import dataclass, field, fields  # noqa: F401
from datetime import UTC, datetime  # noqa: F401
from pathlib import Path  # noqa: F401
from typing import Any  # noqa: F401

from ...config import Config, _is_code_tree  # noqa: F401
from ...core.bookkeeping import STATE_EXCLUDE, is_state  # noqa: F401
from ...core.digest import content_digest, hasher  # noqa: F401
from ...core.model import GATE_OUTCOMES, OUTCOME_MARK, Item, State  # noqa: F401
from ...infra import fsio  # noqa: F401
from ...infra import git as GIT  # noqa: F401
from ...infra import proc as P  # noqa: F401
from ...infra.log import EventLog  # noqa: F401
from .defs import (  # noqa: F401
    _REQUIRED_WARNED,
    DEFAULT_GATES,
    GateDef,
    _required_in_gate_table,
    inert_requirements,
    load_gates,
    pipeline_for,
    pipelined,
    pipelines,
)
from .evidence import (  # noqa: F401
    _NUMSTAT_FIELDS,
    FINGERPRINT_EXCLUDE,
    LEGACY_CLEAN,
    MAX_UNTRACKED_HASHED,
    TreeEntries,
    _blob_id,
    _git_z,
    _hash_into,
    _index_entries,
    _ours,
    _untracked_digest,
    _untracked_paths,
    _working_entry,
    commit_source_tree,
    commit_tree_entries,
    content_id,
    diff_stat,
    differing_paths,
    digest,
    normal_fingerprint,
    source_tree,
    tree_fingerprint,
    worktree_entries,
)
from .mutation import (  # noqa: F401
    REGRESSION_COULD_NOT_RUN,
    REGRESSION_FAILS_ON_FIX,
    REGRESSION_PASSES_ON_PREFIX,
    REGRESSION_VERIFIED,
    MutationResult,
    verify,
    verify_regression_test,
)
from .outcomes import (  # noqa: F401
    GateStatus,
    StaleNote,
    _pass_mark,
    _what_differs,
    approve,
    on_refutation,
    record,
    refuted_line,
    refuted_passes,
    rounds_line,
    stale_evidence,
    stale_evidence_detail,
    status,
    triage_counts,
    triage_line,
)
from .reviewers import (  # noqa: F401
    _BIG_UNTRACKED,
    _OURS,
    REVIEWER_GATES,
    _declared_family,
    _file_digest,
    _unapproved_reviewer,
    _untracked_content_digest,
    family_of,
    git_state,
    git_state_change,
    is_reviewer_gate,
    reviewer_gates,
    reviewer_independence,
    run_watching_git,
)
from .runner import (  # noqa: F401
    _RUN_FIELDS,
    _SHELL_BUILTINS,
    _SHELL_META,
    KEEP_RUN_LOGS,
    RUNS_DIR,
    _account_for_drift,
    _looks_like_not_found,
    _missing_executable,
    _read_text,
    _run_spec,
    classify_exit,
    gate_config_drift,
    run_command_gate,
    run_log_writer,
)
from .testcmd import (  # noqa: F401
    _COMMENT,
    _COUNT,
    _PY_MANIFESTS,
    _PYTEST,
    _PYTEST_NAME,
    _SUMMARY_LINE,
    _VERDICT,
    _XDIST_CHOSEN,
    _XDIST_NAME,
    MAX_SUMMARY_LINES,
    Runner,
    _manifest_texts,
    _named_in,
    counts_of,
    declares_xdist,
    detect,
    file_text,
    last_count_line,
    parallel_test_advice,
    runs_pytest_serially,
    suggested_test_command,
    summary_lines,
    uses_pytest,
)
