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


def _text(item: str, gate: str, rows: list[dict[str, Any]]) -> str:
    counts = {v: sum(1 for r in rows if r["triage"] == v) for v in ("refuted", "confirmed")}
    left = len(rows) - sum(counts.values())
    out = [
        f"{item}.{gate}: {len(rows)} finding(s) -- {counts['refuted']} refuted, "
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
    _log, _cfg, st = _load(repo, agent)

    def bad(why: str) -> O.Outcome:
        return O.failed("review.show", why, id=item, gate=gate, text=why)

    it = st.items.get(item)
    if it is None:
        return bad(f"no such item {item!r}")
    gates = (
        [gate]
        if gate
        else sorted(
            g
            for g, r in it.gates.items()
            if GD.is_triageable(LB.full(r.evidence or {}).get("chunk_findings"))
        )
    )
    if not gates:
        return bad(f"{item} has no `ddflow review` with numbered findings on any gate")
    listing: dict[str, list[dict[str, Any]]] = {}
    for g in gates:
        rows = _rows(it, g)
        if isinstance(rows, str):
            return bad(rows)
        listing[g] = rows
    text = "\n".join(_text(item, g, rows) for g, rows in listing.items())
    return O.ok("review.show", id=item, gate=gate or ",".join(gates), findings=listing, text=text)
