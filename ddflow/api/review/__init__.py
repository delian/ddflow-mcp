"""Cross-family review: run the reviewers configured for a gate and record the outcome.

The rule this family exists to enforce: **an unavailable reviewer is never a passed
review.** No reviewer configured, an empty diff, an endpoint that does not answer — each
records `unavailable`, which is exit 2 and explicitly NOT a pass. Collapsing any of them
into success is the vacuous-pass class at the level of a whole reviewer, and it is the one
failure that looks exactly like a clean bill of health.

`on_progress` exists because a review takes minutes. The CLI prints each line as it
happens; silence for two minutes reads as a hang, and an agent watching a hung tool kills
it. The api streams rather than the surface polling, because only the api knows when a
reviewer has actually answered.
"""

from __future__ import annotations

from ...services.gates.rounds import delta_rounds as _delta_rounds
from ...services.gates.rounds import full_rounds as _full_rounds
from ...services.gates.rounds import kept_copy as _kept_copy
from ...infra import worktree as W
from ...services.gates import reviewed_paths as RP
from ...services.gates.rounds import rounds_used as _rounds_used
from .._base import _load
from ..gates import item_tree

from .diff import _NOISE_PATHSPEC, _NOISE_PREFIXES, commit_diff, SHOWN_UNTRACKED, _base_after_rewrite, diff_for, _tree_diff, _review_branch, _captured  # noqa: F401
from .records import PROGRESS_EVERY_S, _lease_ticker, _say_result, _prior_evidence, _record, _round_evidence, _review_runs, _reason_of, _finding_rows, _item_intent, _last_review, _gate_outcome  # noqa: F401
from .delta import _DELTA_CUT_HOW, _NOTHING_CHANGED, _pick_diff, _delta_cut, _last_head, _kind, _delta_scope, _commits_since, _delta_start, _head_of, _delta_diff, _delta_of_tree, _delta_of_branch  # noqa: F401
from .findings import _finding_listing, _chunk_numbers, _announce_rerun, _hold_unreadable, _digests, _finding_field, _link_refuted, _LINE_CITATION, _design_point, _claim_text, _carry_delta_verdicts, _merge_delta, _untriaged, _replaced_by_rerun, _track_rerun_raised, _SETTLED, _DELTA_KEYS, _hold, _say_triage_scope  # noqa: F401
from .triage import _NODE, _PY_PATH, _RUN_ARG, _BARE_TEST, _defined_anywhere, probe_problem, _where, _bare_problem, _probe_refusal, triage, reviewed_other_tree, _round_and_current_tree, _redo_hint, _reviewed_snapshot, _moved_paths, _name_paths, _settle_mark, _settle_after_cap, _hold_shape, _held_and_settled, _why_outcome, _unchanged_delta, _rerun_scope  # noqa: F401
from .context import _BACKGROUND_HINT, _PREVIOUS_DETAIL, _PREVIOUS_PROBE, _DELTA_CAVEAT, _converges, _floor_for, _floor_lines, _with_previous_findings, _finding_lines, _with_guidance  # noqa: F401
from .scope import _budget_refusal, _superseded, _untriaged_numbers, _justification, _nothing_new, _rounds_report, _mark_partial_retry, _is_partial_retry, _is_fix_round, _scope, _flagged_unchanged_delta, _budget_on_grants, _cite_grant, _grant_ctx, _wants_delta, _budget  # noqa: F401
from .reviewers import reviewers_list, reviewer_block, reviewers_add, reviewers_detect, _ReplyFile  # noqa: F401
from .gate import _order_refusal, _log_started, _unknown_item, _misplaced, _ends_on_signal, _terminated, _record_review, _name_the_item, _say_selection, _early_refusal, _intent_missing, _flag_conflict  # noqa: F401
from .run import review, _LENSES, _review_gates, _same_reviewers, _changed_lines, _any_out_of_rounds, _review_combined, _charged_line, _rounds_for, _review_each, _review_gate  # noqa: F401

__all__ = [
    "_NOISE_PATHSPEC",
    "_NOISE_PREFIXES",
    "commit_diff",
    "SHOWN_UNTRACKED",
    "_base_after_rewrite",
    "diff_for",
    "_tree_diff",
    "_review_branch",
    "_captured",
    "PROGRESS_EVERY_S",
    "_lease_ticker",
    "_say_result",
    "_prior_evidence",
    "_record",
    "_round_evidence",
    "_review_runs",
    "_reason_of",
    "_finding_rows",
    "_item_intent",
    "_last_review",
    "_gate_outcome",
    "_DELTA_CUT_HOW",
    "_NOTHING_CHANGED",
    "_pick_diff",
    "_delta_cut",
    "_last_head",
    "_kind",
    "_delta_scope",
    "_commits_since",
    "_delta_start",
    "_head_of",
    "_delta_diff",
    "_delta_of_tree",
    "_delta_of_branch",
    "_finding_listing",
    "_chunk_numbers",
    "_announce_rerun",
    "_hold_unreadable",
    "_digests",
    "_finding_field",
    "_link_refuted",
    "_LINE_CITATION",
    "_design_point",
    "_claim_text",
    "_carry_delta_verdicts",
    "_merge_delta",
    "_untriaged",
    "_replaced_by_rerun",
    "_track_rerun_raised",
    "_SETTLED",
    "_DELTA_KEYS",
    "_hold",
    "_say_triage_scope",
    "_NODE",
    "_PY_PATH",
    "_RUN_ARG",
    "_BARE_TEST",
    "_defined_anywhere",
    "probe_problem",
    "_where",
    "_bare_problem",
    "_probe_refusal",
    "triage",
    "reviewed_other_tree",
    "_round_and_current_tree",
    "_redo_hint",
    "_reviewed_snapshot",
    "_moved_paths",
    "_name_paths",
    "_settle_mark",
    "_settle_after_cap",
    "_hold_shape",
    "_held_and_settled",
    "_why_outcome",
    "_unchanged_delta",
    "_rerun_scope",
    "_BACKGROUND_HINT",
    "_PREVIOUS_DETAIL",
    "_PREVIOUS_PROBE",
    "_DELTA_CAVEAT",
    "_converges",
    "_floor_for",
    "_floor_lines",
    "_with_previous_findings",
    "_finding_lines",
    "_with_guidance",
    "_budget_refusal",
    "_superseded",
    "_untriaged_numbers",
    "_justification",
    "_nothing_new",
    "_rounds_report",
    "_mark_partial_retry",
    "_is_partial_retry",
    "_is_fix_round",
    "_scope",
    "_flagged_unchanged_delta",
    "_budget_on_grants",
    "_cite_grant",
    "_grant_ctx",
    "_wants_delta",
    "_budget",
    "reviewers_list",
    "reviewer_block",
    "reviewers_add",
    "reviewers_detect",
    "_ReplyFile",
    "_order_refusal",
    "_log_started",
    "_unknown_item",
    "_misplaced",
    "_ends_on_signal",
    "_terminated",
    "_record_review",
    "_name_the_item",
    "_say_selection",
    "_early_refusal",
    "_intent_missing",
    "_flag_conflict",
    "review",
    "_LENSES",
    "_review_gates",
    "_same_reviewers",
    "_changed_lines",
    "_any_out_of_rounds",
    "_review_combined",
    "_charged_line",
    "_rounds_for",
    "_review_each",
    "_review_gate",
]
