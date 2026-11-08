"""A failed review round whose every finding is refuted passes the gate (DX-triage-refuted-gate-pass).

`review triage` used to leave a gate `failed` however it was triaged until the round budget
was spent (`review._settle_after_cap`), so agents refuted every finding and then re-recorded
the gate `passed` by hand -- an unrecorded judgement. Now the triage that gives a failed
round's LAST open finding its verdict, when all of them were refuted, records the gate
passed on refutation once, in the log, with each refutation's probe as evidence (when the
round budget is already spent, `review._settle_after_cap` records the pass first, with counts). A round with
a confirmed finding is not settled by triage alone (the fix wants a re-review), and a gate
the budget already settled is left as it is.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core import outcome as O
from ..services import gates as GD
from ..services import logblobs as LB
from ._base import _load
from .review import _rounds_used, reviewed_other_tree
from .review import triage as _triage


def triage(repo: Path, item: str, **kw: Any) -> O.Outcome:
    """`review.triage`, then the pass-on-refutation when this triage settled the round.

    The pass runs for the gate triaged and any sibling gate the mirror answered too
    (`review.triage`'s `mirrored`), so a combined review's finding stays answered once.
    The `passed_on_refutation` flag is scoped to the gate triaged, not the sibling(s)."""
    out = _triage(repo, item, **kw)
    if out.exit != O.OK:
        return out
    gate = out.data.get("gate", "")
    text = out.data.get("text", "")
    passed = bool(out.data.get("passed_on_refutation"))
    for g in (gate, *(out.data.get("mirrored") or [])):
        log, cfg, st = _load(repo, kw.get("agent", ""))
        it = st.items.get(item)
        rec = it.gates.get(g) if it else None
        if rec is None or rec.outcome != "failed" or not cfg.review.settle_all_refuted:
            continue
        counts = GD.triage_counts(it, g) or {}
        if counts.get("untriaged", 1) or not counts.get("refuted"):
            continue
        # A confirmed finding a later delta round did not raise again is answered (the rule
        # `merge`/`complete` already apply): counting every confirmed one from the merged
        # record leaves a gate stuck failed when its confirmed findings were re-reviewed
        # clean afterwards (bug B-review-gate-stuck-failed-after-triage).
        if GD.unresolved_review_failure(it, g, confirmed_only=True):
            continue
        if stale := reviewed_other_tree(repo, cfg, st, item, g):
            if stale not in text:
                text = f"{text}\n{g} stays {rec.outcome}: {stale}"
            continue
        mine = it.triage.get(g, {})
        found = LB.full(rec.evidence or {}).get("chunk_findings") or []
        refutations = [
            {"finding": n, "digest": f["digest"], "probe": mine[f["digest"]].get("probe", "")}
            for n, f in enumerate(found, 1)
            if mine.get(f["digest"], {}).get("verdict") == "refuted"
        ]
        mark = {
            "passed_on_refutation": {
                "refuted": counts["refuted"],
                "confirmed": counts["confirmed"],
                "rounds": _rounds_used(it, g),
                "max_rounds": cfg.review.max_rounds,
                "refutations": refutations,
            }
        }
        GD.record(
            log,
            cfg,
            item,
            g,
            "passed",
            reason="",
            evidence={**(rec.evidence or {}), **mark},
            by=rec.by,
        )
        text = (
            f"{text}\nrecorded {item}.{g} = passed ON REFUTATION (flagged): "
            f"{counts['refuted']} refuted, {counts['confirmed']} confirmed, not refuted; "
            f"no hand-recording needed"
        )
        if g == gate:
            passed = True
    return O.ok("review.triage", **{**out.data, "text": text, "passed_on_refutation": passed})
