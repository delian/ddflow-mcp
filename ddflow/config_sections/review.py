"""The `[review]` section: its dataclass, the values it accepts and its knob docs.

Re-exported from `ddflow.config`, which assembles `Config` from every section."""

from __future__ import annotations

from dataclasses import dataclass

from ._docs import _doc


@dataclass
class ReviewConfig:
    """The review-round budget (decision D-review-budget): a shipped default for every
    project, so a review loop is bounded without anyone writing a config line."""

    max_rounds: int = 2
    on_exceed: str = "refuse"  # refuse | warn
    delta_default: bool = False


_doc(
    "review",
    "max_rounds",
    'How many FULL cross-family review rounds one gate (rubber_duck, critic) may have on one item (default 2; 0 = unlimited). A round is a review of the item\'s whole diff; later rounds each find fewer defects than the one before, so after the cap the way forward is `ddflow review <id> --gate G --delta` (a recheck of ONLY what changed since the reviewed head) and `ddflow review triage` (refute or confirm each remaining finding with a probe) -- both are always allowed, as is a re-review of named chunks (--chunk). `--force --reason "..."` runs one more full round and records why. Change it for the project (`ddflow config --set review.max_rounds 3`), for this machine (add --local), per run (DDFLOW_REVIEW_MAX_ROUNDS), or over MCP with `ddflow_configure` (the operator is told when an agent does).',
)
_doc(
    "review",
    "on_exceed",
    "What a full round beyond [review].max_rounds does: 'refuse' (default; exit 3, naming --delta, `review triage`, --force --reason and how to change the cap) or 'warn' (run it and say the budget is spent). A delta recheck and triage are never refused either way.",
)
_doc(
    "review",
    "delta_default",
    "Whether `ddflow review <id> --gate G` on a gate that already has a recorded review rechecks ONLY the commits since the head that review covered, instead of the whole diff. Default false (decision D-gate-economy 3): a re-review sends the item's WHOLE diff against its base together with the previous findings and the author's triage of each, so the reviewer confirms each one against the current code and looks for new issues -- a delta that saw only the follow-up commit kept reporting the fix as absent. A re-review is a full round (counted against review.max_rounds). true = the automatic delta: the delta's findings and coverage are merged into the gate's record (earlier findings keep their triage when byte-identical), it is not a full round, and the output says 'delta review of N commits since <sha>'; a rebased branch or a partial prior review falls back to a full round and says why. `--delta` asks for one delta whatever the default (for a very large diff); `--full` forces a full round. Change it for the project (`ddflow config review.delta_default true`), for this machine (add --local), per run (DDFLOW_REVIEW_DELTA_DEFAULT=1), or over MCP with `ddflow_configure` (the operator is told when an agent does).",
)
