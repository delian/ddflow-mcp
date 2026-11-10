"""The workflow-path merge-candidate detector (WP-merge-detect-gates).

Pure ``gate_merge_candidates(state, cfg, item)``: for the item's gate pipeline (the order
its KIND runs) and the gate definitions, the sets of gates that can be merged on the SAME
tree. Three kinds of merge, one proposal set each (D-path-merge-answers):

* ``run_together`` — two processes on the same tree run CONCURRENTLY instead of one after
  the other: the rubber_duck and critic reviews (one review command reviews both; the
  review runner merges them under ``[review].combined_under_lines`` lines, so this
  proposal says they CAN share one review diff, never that they must).
* ``record_together`` — bookkeeping gates that are RECORDED in one call, each keeping its
  OWN outcome and note (research+rules+implement, bug_hunt+dedupe); refused for a gate
  in ``gates.evidence_required`` (it needs evidence of its own run, a different input).
* ``covered_by`` — a gate a larger run already took on this tree, so it is recorded
  covered (unit_tests covered by ci on the same tree, D-test-economy-r2), never silently
  skipped; the covering run must come EARLIER in the pipeline.

Falsifier: a proposal that includes a gate whose INPUTS differ. Each merge group here is
one INPUT CLASS: bookkeeping gates record nothing (one input), the review pair shares the
review diff of one tree, and a covered gate shares the covering run's tree. A group is
proposed only when every member gate is present in the item's pipeline, and no member's
inputs differ from the group's: a bookkeeping member in ``gates.evidence_required`` has an
own-run-evidence input and is dropped from its group.

Research: R-path-merge. The controller consumes this; the proposal-record form is a later
task (text first).
"""

from __future__ import annotations

from collections.abc import Mapping

from ...core.model import State
from ..gates.kinds import kind_pipeline

#: Bookkeeping gates recorded together, in canonical lane order (path_variants.LANES):
#: research+rules+implement, bug_hunt+dedupe. A merged lane keeps ITS OWN outcome and note;
#: refused for a gate that requires evidence of its own run (D-path-merge-answers 1).
RECORD_TOGETHER: tuple[tuple[str, ...], ...] = (
    ("research", "rules", "implement"),
    ("bug_hunt", "dedupe"),
)

#: The review pair that runs concurrently on one tree (D-path-merge-answers 2). The two
#: gates share the review diff of ONE tree, so their inputs never differ within a group.
RUN_TOGETHER: tuple[tuple[str, ...], ...] = (("rubber_duck", "critic"),)

#: gate -> the earlier gate whose run already took it on this tree (covered-by). The covered
#: gate shares the covering run's tree; a covering run on a DIFFERENT tree does NOT cover it.
COVERED_BY: Mapping[str, str] = {"unit_tests": "ci"}

#: The one input class each merge kind reads; a proposal that mixed two of these would be
#: the falsifier (a gate whose inputs differ from the group's) and is never proposed.
BOOKKEEPING_INPUT = "none"
REVIEW_PAIR_INPUT = "review-diff"
COVERED_INPUT = "tree"


def _pipeline(cfg, item) -> list[str]:
    """The gate ids ``item`` runs, in order. An unregistered kind runs the task pipeline."""
    return list(kind_pipeline(cfg, item.kind) if item else [])


def _groups(kind: str, groups: tuple, pipeline: set[str], evidence: set[str]) -> list[dict]:
    """The groups fully present in the item's pipeline; a partial group is not proposed (a
    gate the item does not run is not a candidate), and a bookkeeping group whose member is
    in ``evidence_required`` is dropped (its input is an own run, not 'none')."""
    out = []
    for gates in groups:
        if kind == BOOKKEEPING_INPUT and any(g in evidence for g in gates):
            continue
        if all(g in pipeline for g in gates):
            out.append({"gates": list(gates), "input": kind})
    return out


def gate_merge_candidates(state: State, cfg, item) -> dict:
    """The merge candidates for one item, as plain data. ``state`` is reserved for the
    proposal-record form (a later task); the candidates are derived from ``cfg`` and ``item``
    here."""
    pipe = _pipeline(cfg, item)
    present = set(pipe)
    evidence = set(getattr(cfg.gates, "evidence_required", ()) or ())
    covered = [
        {"gate": covered, "by": covering, "input": COVERED_INPUT}
        for covered, covering in COVERED_BY.items()
        if covered in present
        and covering in present
        and pipe.index(covering) < pipe.index(covered)
    ]
    return {
        "run_together": _groups(REVIEW_PAIR_INPUT, RUN_TOGETHER, present, evidence),
        "record_together": _groups(BOOKKEEPING_INPUT, RECORD_TOGETHER, present, evidence),
        "covered_by": covered,
        "pipeline": pipe,
        "next": {
            "cli": "ddflow gates merge",
            "mcp": "ddflow_gate",
            "note": "text form now; the proposal record is a later task",
        },
    }


def has_figures(data: dict) -> bool:
    """True when any merge group was proposed (else the answer is exit 2)."""
    return bool(data["run_together"] or data["record_together"] or data["covered_by"])


def text(data: dict) -> str:
    """The human form: one line per proposed merge, then the next call."""
    out = ["gate merge candidates"]
    for r in data["run_together"]:
        out.append(f"  run together: {', '.join(r['gates'])}")
    for r in data["record_together"]:
        out.append(f"  record together: {', '.join(r['gates'])}")
    for r in data["covered_by"]:
        out.append(f"  covered: {r['gate']} by {r['by']}")
    if not has_figures(data):
        out.append("  (none: no merge candidates in the pipeline)")
    out.append(f"next: {data['next']['cli']}  |  {data['next']['mcp']}")
    return "\n".join(out)


__all__ = [
    "BOOKKEEPING_INPUT",
    "COVERED_BY",
    "COVERED_INPUT",
    "RECORD_TOGETHER",
    "REVIEW_PAIR_INPUT",
    "RUN_TOGETHER",
    "gate_merge_candidates",
    "has_figures",
    "text",
]
