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
    delta_default: bool = True


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
    "Whether `ddflow review <id> --gate G` on a gate that already has a recorded review rechecks ONLY the commits since the head that review covered (default true) instead of the whole diff again. The delta's findings and coverage are merged into the gate's record (earlier findings keep their triage when byte-identical), it is not a full round, and the output says 'delta review of N commits since <sha>' so it is never mistaken for a full pass. `--full` forces a full round (counted against review.max_rounds); a branch that was rebased since (the reviewed head is no ancestor) and, for the automatic delta, a prior review that was partial fall back to a full round and say why; a review that never reached a reviewer records no reviewed head, so the next delta starts from the last real one. false = every review is a full round, the behaviour before this knob. Change it for the project (`ddflow config review.delta_default false`), for this machine (add --local), per run (DDFLOW_REVIEW_DELTA_DEFAULT=0), or over MCP with `ddflow_configure` (the operator is told when an agent does).",
)
