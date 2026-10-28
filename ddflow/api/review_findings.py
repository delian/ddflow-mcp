"""`ddflow review show <item> --gate G`: the numbered findings of a gate's recorded review.

A review's findings used to print only in the output of the running `ddflow review` -- lost
if stdout was not kept -- while `review triage` needs a finding NUMBER, so reading finding N
meant digging `chunk_findings` out of an event-log line. This reads the recorded round back:
each finding with its number (the one triage takes), severity, location, digest and what
became of it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core import blobref as B
from ..core import outcome as O
from ..services import gates as GD
from ..services import logblobs as LB
from ..services.gates.rounds import delta_rounds, full_rounds, rounds_used
from ._base import _load


def _rows(it, gate: str) -> list[dict[str, Any]] | str:
    """The findings of ``gate`` as rows, or why there are none to list."""
    rec = it.gates.get(gate)
    found = LB.full(rec.evidence or {}).get("chunk_findings") if rec else None
    if isinstance(found, B.Unavailable):
        return f"the findings of {it.id}.{gate} are {found}: the blob must be restored first"
    if not GD.is_triageable(found):
        return (
            f"{it.id}.{gate} has no `ddflow review` with numbered findings on record "
            "(a hand-recorded gate, a clean review, or one from before triage)"
        )
    mine = it.triage.get(gate, {})
    rows = []
    for n, f in enumerate(found, 1):
        t = mine.get(f.get("digest"), {})
        rows.append(
            {
                "n": n,
                "severity": f.get("severity", ""),
                "location": f.get("location", ""),
                "digest": f.get("digest", ""),
                "chunk": f.get("chunk", ""),
                "detail": f.get("detail", ""),
                "triage": t.get("verdict", "") or "untriaged",
                "probe": t.get("probe", ""),
                "triaged_by": t.get("by", ""),
            }
        )
    return rows


def _fold(cfg, it, gate: str) -> str:
    """The round count `review show` names: the SAME fold `gate status` and the review
    budget count (DX-review-round-count), so every view of one gate reports one number."""
    used = rounds_used(it, gate)
    cap = cfg.review.max_rounds
    note = f"{used} of {cap} rounds" if cap > 0 else f"{used} rounds (unlimited)"
    full, delta = full_rounds(it, gate), delta_rounds(it, gate)
    return f"{note} -- {full} full, {delta} delta"


def _text(item: str, gate: str, rows: list[dict[str, Any]], fold: str) -> str:
    counts = {v: sum(1 for r in rows if r["triage"] == v) for v in ("refuted", "confirmed")}
    left = len(rows) - sum(counts.values())
    out = [
        f"{item}.{gate}: {fold} -- {len(rows)} finding(s) -- {counts['refuted']} refuted, "
        f"{counts['confirmed']} confirmed, {left} untriaged"
    ]
    for r in rows:
        out.append(
            f"  #{r['n']} [{r['severity']}] {r['location']}  digest {r['digest']}  "
            f"{r['triage'].upper()}"
        )
        out.extend(f"      {ln}" for ln in str(r["detail"]).splitlines() or [""])
        if r["probe"]:
            out.append(f"      probe ({r['triaged_by']}): {r['probe']}")
    return "\n".join(out)


def show(repo: Path, item: str, *, gate: str = "", agent: str = "") -> O.Outcome:
    """The recorded round's findings for ``gate`` (every gate that has some when omitted)."""
    _log, cfg, st = _load(repo, agent)

    def bad(why: str) -> O.Outcome:
        return O.failed("review.show", why, id=item, gate=gate, text=why)

    it = st.items.get(item)
    if it is None:
        return bad(f"no such item {item!r}")

    def has_round(r) -> bool:
        found = LB.full(r.evidence or {}).get("chunk_findings")
        return isinstance(found, B.Unavailable) or GD.is_triageable(found)

    gates = [gate] if gate else sorted(g for g, r in it.gates.items() if has_round(r))
    if not gates:
        return bad(f"{item} has no `ddflow review` with numbered findings on any gate")
    listing: dict[str, list[dict[str, Any]]] = {}
    unreadable: dict[str, str] = {}
    for g in gates:
        rows = _rows(it, g)
        if isinstance(rows, str):
            if gate:  # asked for by name: the answer is the reason
                return bad(rows)
            unreadable[g] = rows  # one gate's lost blob must not hide the others
            continue
        listing[g] = rows
    text = "\n".join(
        [_text(item, g, rows, _fold(cfg, it, g)) for g, rows in listing.items()]
        + [f"{g}: {why}" for g, why in unreadable.items()]
    )
    if not listing:
        return bad(text)
    return O.ok(
        "review.show",
        id=item,
        gate=gate or ",".join(gates),
        findings=listing,
        rounds={
            g: {
                "used": rounds_used(it, g),
                "max_rounds": cfg.review.max_rounds,
                "full": full_rounds(it, g),
                "delta": delta_rounds(it, g),
            }
            for g in listing
        },
        unreadable=unreadable,
        text=text,
    )
