"""`ddflow bug dedupe`: cluster near-duplicate OPEN bugs and, with ``--apply``, link them.

The sweep `ddflow dupes` already offers every unsettled near-duplicate PAIR in the log
(`pairs.dupes`); this is the OPEN-bugs fan-out the operator asked for after dx-autofile-defects
let one root cause be filed under many titles: cluster the OPEN list, name a canonical
member per cluster and its shared cause, and -- only when asked, ``--apply`` -- link each
younger member onto the canonical with the SAME ``link_record`` `ddflow link --duplicate-of`
uses. Nothing is closed here: a duplicate bug is closed only once its original is fixed
(B-dup-closure); the links settle the pairs so neither `dupes` nor the next sweep re-offers
them.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...core import outcome as O
from ...services import bugsim
from .._base import _load
from .pairs import _settled, link_record


def _dupe_entry(canonical: str, did: str, scores, fl: float) -> dict[str, Any]:
    """One duplicate row: its id and its best score to the canonical member."""
    k = (canonical, did) if canonical < did else (did, canonical)
    return {"id": did, "score": scores.get(k, fl)}


def dedupe_bugs(
    repo: Path,
    *,
    apply: bool = False,
    floor: float | None = None,
    agent: str = "",
) -> O.Outcome:
    """Cluster the OPEN bugs by symptom + root cause and report the clusters.

    ``floor`` defaults to ``[dedupe].show_floor`` (a prompt to LOOK, as every sweep is).
    The canonical member of a cluster is its OLDEST (first found); a duplicate is anything
    younger. ``apply`` records ``link <dup> --duplicate-of <canonical>`` for each duplicate
    (each link settles one pair; a bug is never closed by it).
    """
    _log, cfg, st = _load(repo, agent)
    fl = float(cfg.dedupe.show_floor) if floor is None else float(floor)
    bugs = sorted((b for b in st.bugs.values() if b.open), key=lambda b: (b.found_at, b.id))
    records = [
        {"id": b.id, "kind": "bug", "title": b.title, "body": b.summary, "item": b.item}
        for b in bugs
    ]
    if not records:
        return O.nothing(
            "bug.dedupe",
            "no OPEN bugs to scan",
            clusters=[],
            count=0,
            applied=[],
            floor=fl,
        )
    vectors, note = bugsim.embed_vectors(repo, cfg, [bugsim.record_text(r) for r in records])
    groups, scores = bugsim.cluster(records, floor=fl, vectors=vectors)
    clusters: list[dict[str, Any]] = []
    applied: list[str] = []
    failed: list[str] = []
    for group in groups:
        canonical = group[0]
        dupes = []
        for did in group[1:]:
            if _settled(st, canonical, did):
                continue  # already linked or dismissed: reported once, never linked twice
            dupes.append(_dupe_entry(canonical, did, scores, fl))
        if not dupes:
            continue  # every pair of the cluster was already settled: nothing left to offer
        clusters.append(
            {
                "canonical": canonical,
                "duplicates": dupes,
                "cause": bugsim.shared_cause(records, group),
            }
        )
        if apply:
            for dup in dupes:
                out = link_record(
                    repo,
                    dup["id"],
                    "duplicate_of",
                    canonical,
                    reason="bug dedupe --apply",
                    agent=agent,
                )
                (applied if out.ok else failed).append(dup["id"])
    data: dict[str, Any] = {
        "clusters": clusters,
        "count": len(clusters),
        "applied": applied,
        "failed": failed,
        "floor": fl,
        "semantic": note or "semantic + overlap",
        "scanned": len(records),
    }
    if not clusters:
        return O.nothing(
            "bug.dedupe",
            f"no near-duplicate OPEN bug cluster at floor {fl:g}",
            **data,
        )
    return O.ok("bug.dedupe", **data)
