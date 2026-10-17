"""A failed review gate is not a finished one (decision D-failed-review-gate, bug B85841c0846).

`merge` and `complete` refuse a review gate whose latest outcome is failed unless every
confirmed finding is followed by a later delta round that did not raise it again."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from ...config import Config
from ...core.model import State
from .. import logblobs as LB
from .defs import DEFAULT_GATES, GateDef, pipeline_for
from .outcomes import is_triageable, triage_counts

#: How a blocker of `failed_review_blockers` begins; `complete` never lifts these with
#: `--force` (decision D-failed-review-gate): only the operator's `gate skip --reason` does.
FAILED_REVIEW_BLOCKER = "review gate(s) failed with findings not re-reviewed: "


def _digests_of(evidence: Mapping[str, Any]) -> set[str]:
    found = LB.full(evidence or {}).get("chunk_findings")
    if not is_triageable(found):
        return set()
    return {f["digest"] for f in found}


def _is_delta_round(ev: Mapping[str, Any]) -> bool:
    """A delta round that reached a reviewer in full and recorded what it raised itself."""
    return (
        ev.get("review_kind") == "delta" and ev.get("status") == "REVIEWED" and "delta_raised" in ev
    )


def _cleared_after_delta(runs: list, digest: str) -> bool:
    """Is ``digest`` answered: after the LAST round that raised it, did a complete delta
    round run that did not raise it again? A delta records the digests it raised itself
    (`delta_raised`); any other round that carries the finding (a full round, a hand
    record) raises it afresh and undoes an earlier clearing."""
    first = next((i for i, r in enumerate(runs) if digest in _digests_of(r.evidence)), None)
    if first is None:
        return False
    cleared = False
    for run in runs[first + 1 :]:
        ev = run.evidence or {}
        if _is_delta_round(ev):
            cleared = digest not in set(ev["delta_raised"] or [])
        elif digest in _digests_of(ev):
            cleared = False
    return cleared


def unresolved_review_failure(it, gate: str) -> str:
    """Why ``gate``'s FAILED outcome is still unanswered, or "" (decision
    D-failed-review-gate): a gate not failed, one whose findings were all refuted, or one
    whose every confirmed finding a later delta round did not raise again, is answered.
    Findings without a verdict, and a failure with no numbered findings (a hand record),
    are not: nothing re-reviewed them."""
    if it.gate_outcome(gate) != "failed":
        return ""
    counts = triage_counts(it, gate)
    if counts is None:
        return "failed with no triaged findings on record"
    if counts["untriaged"]:
        return f"{counts['untriaged']} finding(s) without a verdict"
    rec = it.gates[gate]
    found = LB.full(rec.evidence or {}).get("chunk_findings") or []
    mine = it.triage.get(gate, {})
    runs = it.runs_of(gate)
    left = [
        f"#{n}"
        for n, f in enumerate(found, 1)
        if mine.get(f["digest"], {}).get("verdict") == "confirmed"
        and not _cleared_after_delta(runs, f["digest"])
    ]
    if not left:
        return ""
    return (
        f"confirmed finding(s) {', '.join(left)} have no later delta round that did not raise them"
    )


def failed_review_blockers(
    state: State,
    cfg: Config,
    item_id: str,
    defs: Mapping[str, GateDef] | None,
    skip: Iterable[str] = (),
) -> list[str]:
    """One blocker for the review gates (`reviewer = "different_family"`) of ``item_id``'s
    pipeline that are FAILED and unanswered (`unresolved_review_failure`). ``skip`` are the
    gates the actual diff excused. The operator's way past is `gate skip --reason`."""
    it = state.items.get(item_id)
    if it is None:
        return []
    excused = set(skip)
    rows = []
    for g in pipeline_for(it, cfg):
        gd = (defs or {}).get(g) or DEFAULT_GATES.get(g)
        if g in excused or gd is None or gd.reviewer != "different_family":
            continue
        if why := unresolved_review_failure(it, g):
            rows.append(f"{g} ({why})")
    if not rows:
        return []
    return [
        FAILED_REVIEW_BLOCKER
        + "; ".join(rows)
        + f". Fix and re-run `ddflow review {item_id} --gate <gate>` (a delta round; a fix "
        f"round is free of max_rounds), or the OPERATOR records the reason with "
        f"`ddflow gate skip {item_id} <gate> --reason ...` -- `--force` does not lift this."
    ]
