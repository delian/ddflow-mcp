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

# The gate machinery lives one module per area in this package (B-uni-splits); the public
# functions and classes are
# re-exported, so `from ddflow.services import gates as G; G.run_command_gate(...)` keeps
# working. A REBINDING is not shared: each function reads names from its own module, so to
# replace a helper or a constant in a test, patch the module that defines it
# (`gates.evidence.MAX_UNTRACKED_HASHED`), not this package.

from __future__ import annotations

from .defs import (  # noqa: F401
    DEFAULT_GATES,
    GateDef,
    inert_requirements,
    load_gates,
    pipeline_for,
    pipelined,
    pipelines,
    required_gates,
)
from .evidence import (  # noqa: F401
    FINGERPRINT_EXCLUDE,
    LEGACY_CLEAN,
    MAX_UNTRACKED_HASHED,
    UNLISTED,
    TreeEntries,
    commit_source_tree,
    commit_tree_entries,
    content_id,
    diff_stat,
    differing_paths,
    digest,
    is_tree,
    normal_fingerprint,
    recorded_content,
    source_tree,
    tree_being_completed,
    tree_fingerprint,
    tree_identity,
    worktree_entries,
)
from .kinds import (  # noqa: F401
    BOILERPLATE,
    EVIDENCE_FORMS,
    KINDS,
    REPORT_KEYS,
    evidence_forms,
    evidence_problems,
    gate_applies,
    kind_pipeline,
    kind_pipelines,
    not_applicable,
    record_owner,
)
from .measured import (  # noqa: F401
    Order,
    check_order,
    gates_ahead_of,
    measure_tree,
    order_note,
    record_measured,
    record_merge,
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
    REVIEWER_GATES,
    family_for,
    family_of,
    git_state,
    git_state_change,
    is_reviewer_gate,
    reviewer_gates,
    reviewer_independence,
    router_set,
    run_watching_git,
)
from .runner import (  # noqa: F401
    KEEP_RUN_LOGS,
    OUTPUT_TAIL_CHARS,
    RUNS_DIR,
    Classified,
    classify_exit,
    gate_config_drift,
    output_evidence,
    run_command_gate,
    run_log_writer,
)
from .testcmd import (  # noqa: F401
    MAX_SUMMARY_LINES,
    Runner,
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
