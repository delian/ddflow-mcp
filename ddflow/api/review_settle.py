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
from .review import _rounds_used
from .review import triage as _triage


def triage(repo: Path, item: str, **kw: Any) -> O.Outcome:
    """`review.triage`, then the pass-on-refutation when this triage settled the round."""
    out = _triage(repo, item, **kw)
    if out.exit != O.OK:
        return out
    gate = out.data.get("gate", "")
    log, cfg, st = _load(repo, kw.get("agent", ""))
    it = st.items.get(item)
    rec = it.gates.get(gate) if it else None
    if rec is None or rec.outcome != "failed" or not cfg.review.settle_all_refuted:
        return out
    counts = GD.triage_counts(it, gate) or {}
    if counts.get("untriaged", 1) or counts.get("confirmed") or not counts.get("refuted"):
        return out
    mine = it.triage.get(gate, {})
    found = LB.full(rec.evidence or {}).get("chunk_findings") or []
    refutations = [
        {"finding": n, "digest": f["digest"], "probe": mine[f["digest"]].get("probe", "")}
        for n, f in enumerate(found, 1)
        if mine.get(f["digest"], {}).get("verdict") == "refuted"
    ]
    mark = {
        "passed_on_refutation": {
            "refuted": counts["refuted"],
            "confirmed": 0,
            "rounds": _rounds_used(it, gate),
            "max_rounds": cfg.review.max_rounds,
            "refutations": refutations,
        }
    }
    GD.record(
        log,
        cfg,
        item,
        gate,
        "passed",
        reason="",
        evidence={**(rec.evidence or {}), **mark},
        by=rec.by,
    )
    note = (
        f"recorded {item}.{gate} = passed ON REFUTATION (flagged): every one of the "
        f"{counts['findings']} finding(s) was refuted with its probe; no hand-recording needed"
    )
    return O.ok(
        "review.triage",
        **{**out.data, "text": f"{out.data['text']}\n{note}", "passed_on_refutation": True},
    )
