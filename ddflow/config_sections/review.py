"""The `[review]` section: its dataclass, the values it accepts and its knob docs.

Re-exported from `ddflow.config`, which assembles `Config` from every section."""

from __future__ import annotations

from dataclasses import dataclass

from ._docs import _doc, declare, knob


@declare("review")
@dataclass
class ReviewConfig:
    """The review-round budget (decision D-review-budget): a shipped default for every
    project, so a review loop is bounded without anyone writing a config line."""

    max_rounds: int = 2
    on_exceed: str = "refuse"  # refuse | warn
    delta_default: bool = False
    combined_under_lines: int = knob(
        150,
        doc="`ddflow review <id> --gate rubber_duck,critic` on a change with fewer changed lines than this (default 150; 0 = never) sends ONE review request whose prompt carries both gates' lenses and records its outcome for each gate, with a shared `review_id` in their evidence (decision D-gate-economy 2: a small fix spent 2.9 rounds of each gate, most failing on findings later refuted). A larger change keeps one review per gate, one after the other. Not for --chunk, --delta, --commit or --base, nor when either gate is out of rounds, the two are served by different reviewers, or a gate is not rubber_duck or critic; the combined review is a full round of each gate. Change it for the project (`ddflow config review.combined_under_lines 100`), for this machine (add --local), per run (DDFLOW_REVIEW_COMBINED_UNDER_LINES), or over MCP with `ddflow_configure`.",
    )


_doc(
    "review",
    "max_rounds",
    'How many cross-family review rounds one gate (rubber_duck, critic) may have on one item, full and delta alike (default 2; 0 = unlimited; decision D-gate-economy 2 -- a delta after the cap was the way around it). Later rounds each find fewer defects than the one before, so after the cap the way forward is `ddflow review triage` (refute each remaining finding with the run that shows it false, or confirm it with the test that now passes; always allowed), then recording the gate on that triage or asking the operator. A re-review of named chunks (--chunk) repeats part of a recorded round and is not counted. `--force --reason "..."` runs one more round and records why. Change it for the project (`ddflow config --set review.max_rounds 3`), for this machine (add --local), per run (DDFLOW_REVIEW_MAX_ROUNDS), or over MCP with `ddflow_configure` (the operator is told when an agent does).',
)
_doc(
    "review",
    "on_exceed",
    "What a round beyond [review].max_rounds does: 'refuse' (default; exit 3, naming `review triage`, --force --reason and how to change the cap) or 'warn' (run it and say the budget is spent). `review triage` is never refused either way; a delta counts against the cap like any round.",
)
_doc(
    "review",
    "delta_default",
    "Whether `ddflow review <id> --gate G` on a gate that already has a recorded review rechecks ONLY the commits since the head that review covered, instead of the whole diff. Default false (decision D-gate-economy 3): a re-review sends the item's WHOLE diff against its base together with the previous findings and the author's triage of each, so the reviewer confirms each one against the current code and looks for new issues -- a delta that saw only the follow-up commit kept reporting the fix as absent. A re-review is a full round (counted against review.max_rounds). true = the automatic delta: the delta's findings and coverage are merged into the gate's record (earlier findings keep their triage when byte-identical), it is not a full round but counts against review.max_rounds, and the output says 'delta review of N commits since <sha>'; a rebased branch or a partial prior review falls back to a full round and says why. `--delta` asks for one delta whatever the default (for a very large diff); `--full` forces a full round. Change it for the project (`ddflow config review.delta_default true`), for this machine (add --local), per run (DDFLOW_REVIEW_DELTA_DEFAULT=1), or over MCP with `ddflow_configure` (the operator is told when an agent does).",
)
