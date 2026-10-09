"""The one path a gate outcome takes into the log: order check, tree measurement, guard.

Five families record gate outcomes -- `gate record`/`run`/`skip`, `review`, the local
`merge`, the forge sync in `flow` -- and each used to decide for itself how much of this to
do: only `gate record` measured the tree, only `gate record` and `gate run` checked the
pipeline order, and which of them reached the human-gate guard depended on whether the
caller remembered to pass the gate definitions (bug B279a0ebfc1).

* ``check_order`` is the pipeline-order check (`gates.enforce_order`), asked BEFORE the work
  a gate stands for: a review run before `implement` reviewed an empty diff.
* ``measure_tree`` is what ddflow itself determines about the item's tree.
* ``record_measured`` measures that tree and writes the outcome through
  `outcomes.record`, which holds the human-gate guard and the evidence contract. It does not
  check the order: an act that has already happened (a merge, a forge merge, a command that
  ran) is recorded whatever the order, and the check belongs before it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ...config import Config
from ...core.model import Item, State
from ...infra.log import EventLog
from .defs import GateDef
from .evidence import (
    _untracked_paths,
    commit_source_tree,
    content_id,
    diff_stat,
    source_tree,
    tree_being_completed,
    tree_identity,
    worktree_entries,
)
from .outcomes import record, status


@dataclass(frozen=True)
class Order:
    """What the order check found: the earlier gates with no outcome yet (``ahead``), and,
    when `enforce_order = "block"` makes that a refusal, the sentence for the caller."""

    ahead: list[str] = field(default_factory=list)
    refusal: str = ""

    def note(self, gate: str, policy: str) -> str:
        """The `warn` sentence; empty when nothing is ahead."""
        if not self.ahead:
            return ""
        return f"NOTE: {order_note(self.ahead, gate)} Recording anyway ([gates].enforce_order = '{policy}')."


def gates_ahead_of(
    st: State,
    cfg: Config,
    item_id: str,
    gate: str,
    defs: Mapping[str, GateDef] | None = None,
) -> list[str]:
    """Pipeline gates BEFORE ``gate`` that have no outcome yet.

    The order in `gates.task_pipeline` is not decoration: a rubber-duck review recorded
    before `implement` reviewed an empty diff, and a `merge` recorded before `unit_tests`
    merged something nobody tested. A gate that does not apply to the item (``defs``,
    `applies_when`) is not ahead of anything.
    """
    try:
        s = status(st, cfg, item_id, defs)
    except KeyError:
        return []
    if gate not in s.pipeline:
        return []
    before = s.pipeline[: s.pipeline.index(gate)]
    it = st.items.get(item_id)
    return [g for g in before if it and not it.gate_outcome(g) and g not in s.not_applicable]


def order_note(ahead: list[str], gate: str) -> str:
    return (
        f"{gate} comes after {', '.join(ahead)} in the pipeline, and "
        f"{'none of those have' if len(ahead) > 1 else 'that one has not'} run yet."
    )


def check_order(
    log: EventLog,
    cfg: Config,
    st: State,
    item: str,
    gate: str,
    *,
    recording: bool,
    defs: Mapping[str, GateDef] | None = None,
) -> Order:
    """Enforce `gates.enforce_order`, and RECORD the violation (``recording``) either way.

    A refusal has ``refusal`` set and records nothing. Under `warn` the violation is
    appended as `gate.out_of_order` when ``recording`` -- whether `warn` should become
    `block` or `off` is a judgement about how often this fires, and for as long as it only
    printed, that judgement had no evidence behind it either way.
    """
    ahead = gates_ahead_of(st, cfg, item, gate, defs)
    if not ahead or cfg.gates.enforce_order == "off":
        return Order()
    if cfg.gates.enforce_order == "block":
        return Order(
            ahead,
            refusal=(
                f"{order_note(ahead, gate)}\nThe order is the point: reviewing a change "
                f"before it is implemented reviews nothing. Run them in order, or set "
                f"[gates].enforce_order = 'warn'."
            ),
        )
    if recording:
        log.append(
            "gate.out_of_order",
            item,
            {"gate": gate, "ahead": ahead, "policy": cfg.gates.enforce_order},
        )
    return Order(ahead)


def measure_tree(repo: Path, it: Item, wt: Path | None) -> dict[str, Any]:
    """What ddflow measures for a recorded gate: the item's tree. Once the item has
    landed and its tree was kept, a tree that differs from what landed ONLY by untracked
    files (scratch that never landed) is measured as what landed: those files made every
    gate recorded after the merge read as stale at complete (Bb47a48b173). Any other
    difference -- work committed or edited after the landing -- is kept, so complete
    still reports it."""
    if not wt:
        return {}
    measured = {"tree_sha": tree_identity(wt), "diff_stat": diff_stat(wt)}
    measured.update(_landed_if_only_untracked_differs(repo, it, wt))
    return measured


def _landed_if_only_untracked_differs(repo: Path, it: Item, wt: Path) -> dict[str, str]:
    """`tree_sha` of the landed commit when ``wt`` holds exactly that content plus untracked
    files; {} otherwise (not landed, the same already, or a real change)."""
    if not (it.landed_after or it.merged_sha):
        return {}
    _cwd, landed = tree_being_completed(repo, it)
    source = commit_source_tree(repo, landed) if landed else ""
    if not source or source == source_tree(wt):
        return {}
    entries = worktree_entries(wt)
    if entries is None:
        return {}
    untracked = set(_untracked_paths(wt))
    tracked_only = {p: e for p, e in entries.items() if p not in untracked}
    if content_id(tracked_only) != source:
        return {}  # the tree changed beyond scratch: let complete say so
    return {"tree_sha": f"{landed[:12]}+clean"}


def record_measured(  # noqa: PLR0913 -- the same keywords `outcomes.record` takes, plus where the item's work is
    log: EventLog,
    cfg: Config,
    repo: Path,
    it: Item,
    gate: str,
    outcome: str,
    *,
    gates: dict[str, GateDef],
    tree: Path | None = None,
    by: str = "",
    reason: str = "",
    evidence: dict[str, Any] | None = None,
) -> None:
    """Record ``outcome`` for ``it``'s ``gate``, with ddflow's own measurement of ``tree``
    (the item's work; None when it cannot be told or the caller's evidence already carries
    the measurement of the tree a command ran on, and a skip is never measured: nothing was
    inspected).

    Raises ``ValueError`` as `outcomes.record` does: a human gate that an agent tries to
    clear, a bare pass where evidence is required, a non-pass with no reason. ``gates`` is
    required, not optional: leaving it out is how a record path skipped the human-gate
    guard.
    """
    taken = {} if outcome == "skipped" else measure_tree(repo, it, tree)
    record(
        log,
        cfg,
        it.id,
        gate,
        outcome,
        by=by,
        reason=reason,
        evidence=evidence,
        gates=gates,
        measured=taken,
    )


def record_merge(
    log: EventLog,
    cfg: Config,
    repo: Path,
    it: Item,
    outcome: str,
    *,
    gates: dict[str, GateDef],
    tree: Path | None = None,
    reason: str = "",
    evidence: dict[str, Any] | None = None,
) -> bool:
    """Record the ``merge`` gate's outcome after the act of merging -- a local merge, a
    forge merge seen by `pr sync`. Returns True, recording NOTHING, when `merge` is a HUMAN
    checkpoint: the person clears it, and an agent's outcome (`failed` above all) reads as
    their rejection. The caller says so in its report instead.

    No order check: the merge has happened, and refusing to write that down would only
    hide it.
    """
    gdef = gates.get("merge")
    if gdef is not None and gdef.is_human_gate:
        return True
    record_measured(
        log,
        cfg,
        repo,
        it,
        "merge",
        outcome,
        gates=gates,
        tree=tree,
        reason=reason,
        evidence=evidence,
    )
    return False
