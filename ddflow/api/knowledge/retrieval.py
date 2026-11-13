"""Finding what the project already knows: `recall` and `similar`."""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import ddflow.api._dedupe as DD

from ...config import csv_list
from ...core import outcome as O
from ...core import participation as PA
from ...core import provenance as PV
from ...core.budget import RECALL_MAX_CHARS, Budget
from ...infra.store import recall_sources, similar_records
from ...services import bugsim
from ...services import contextpack as CP
from ...services import searchcore as SC
from ...services import similar as sim
from ...services.egress import Egress
from .._base import _load
from .lessons import _store


def _origin(st, table: str, ident):
    """The `Origin` of a recalled decision, lesson or memory; None for the other kinds."""

    if table == "decisions" and ident in st.decisions:
        return PV.decision_origin(st.decisions[ident])
    if table == "lessons" and ident in st.lessons:
        return PV.lesson_origin(st.lessons[ident])
    if table == "memories" and ident in st.memories:
        return PV.memory_origin(st.memories[ident])
    declared = PA.DECLARED.get(table)
    rec = st.defs.get(f"{declared.def_kind}:{ident}") if declared else None
    if rec is not None:  # a declared kind: somebody's words, said by an agent or an import
        return PV.Origin(PV.IMPORTED if rec.source else PV.AGENT, rec.by, rec.source)
    return None


#: The old name of the sources search, which moved to the search core.
_search_sources = SC.search_sources


def recall(
    repo: Path,
    query: str,
    *,
    sources: str = "",
    limit: int = 3,
    max_chars: int = RECALL_MAX_CHARS,
    agent: str = "",
) -> O.Outcome:
    """ "Have we been here before?" — one query across everything the project remembers.

    Searches architectural decisions, lessons, research verdicts, past bugs, similar
    tasks and the operator's own earlier prompts, and labels each hit by WHAT KIND of
    thing it is — because "should this change what I do" has a different answer for a
    binding decision, a transferable lesson and a prompt from three weeks ago.

    Exists so an operator does not have to say the same thing twice and an agent does not
    have to learn the same thing twice. Both failures are invisible in the moment and
    obvious in the log.
    """
    log, cfg, _st = _load(repo, agent)
    sources_now = recall_sources()
    results = SC.search_sources(_store(repo, log, cfg), query, sources, limit)
    labels = {table: label for table, label, _ in sources_now}
    # a record whose sensitivity may not go to a model is withheld (services/egress.py)
    gate = Egress(repo, cfg)
    for table in list(results):
        dk = PA.DECLARED[table].def_kind if table in PA.DECLARED else ""
        kept = [
            r
            for r in results[table]
            if not (dk and gate.withholds_def("recall", _st, dk, r.get("id")))
        ]
        results[table] = kept
        if not kept:
            del results[table]
    # Who recorded each hit (`core/provenance.py`): decisions, lessons and memories are
    # somebody's words, and a hit shown without its author reads as the tool's own.
    # Looked up in the folded state by id -- the index holds no author column.
    for table, rows in results.items():
        for r in rows:
            o = _origin(_st, table, r.get("id"))
            if o is not None:
                r["provenance"] = {"trust": o.trust, "by": o.by, "source": o.source}
    # One budget, enforced here for every caller (`services/contextpack.py`): the CLI prints
    # and the MCP tool returns what the pack kept.
    cut = CP.pack(
        {
            table: [CP.candidate(table, labels[table], r) for r in rows]
            for table, rows in results.items()
        },
        Budget(max_chars, "chars"),
    )
    results = {t: [c.row for c in cs] for t, cs in cut.kept.items()}
    data: dict[str, Any] = {
        "results": {t: [c.hit for c in cs] for t, cs in cut.kept.items()},
        "query": query,
        "searched": [t for t, _, _ in sources_now],
        "max_chars": max_chars,
        "pack": {
            "shown": cut.shown,
            "total": cut.total,
            "duplicates": cut.duplicates,
            "truncated": cut.truncated,
            "note": cut.note(),
            "cut_note": cut.cut_note(),
            "fold_note": cut.fold_note(),
            "cited": list(cut.cited),
        },
        "_render": {"results": results, "sources": sources_now},
    }
    if not results:
        return O.nothing(
            "recall",
            f"Nothing recalled for {query!r}.\n"
            f"Searched: {', '.join(t for t, _, _ in sources_now)}.",
            **data,
        )
    # What the budget or the duplicate check left out is said where a caller reads it: the
    # MCP result's second block. The CLI prints its own line under the records.
    return O.Outcome(kind="recall", data=data, reason=cut.note())


def _semantic_bug_rows(repo: Path, cfg, st, text: str, rows: list[dict]) -> str:
    """Fold the semantic matcher's bug candidates into ``rows`` (in place), and Return the
    note ("" when the embedder ran). Strengthened for bugs (DX-bug-dedupe-sweep): a
    differently-worded report of one defect the token engine missed is surfaced; its score
    is folded into the candidate, and a bug only the embedder sees is added."""
    bugs = [r for r in similar_records(st) if r["kind"] == "bug"]
    hits, note = bugsim.semantic_hits(repo, cfg, text, bugs, floor=cfg.dedupe.show_floor)
    sem = dict(hits)
    have = {r["id"] for r in rows if r["kind"] == "bug"}
    for r in rows:
        if r["kind"] == "bug" and r["id"] in sem:
            r["semantic"] = sem[r["id"]]
            r["score"] = max(r["score"], r["semantic"])
    for bid, s in hits:
        if bid in have:
            continue
        src = next((x for x in bugs if x["id"] == bid), None)
        headline, state = DD.record_state(st, bid, "bug", grace_s=cfg.lease.grace_s)
        rows.append(
            {
                "id": bid,
                "kind": "bug",
                "title": " ".join((headline or (src["body"] if src else "")).split())[:200],
                "state": state,
                "score": s,
                "semantic": s,
                "shared": [],
                "flags": ["semantic"],
            }
        )
    rows.sort(key=lambda r: (-r["score"], r["id"]))
    return note


def similar(
    repo: Path, text: str, *, kinds: str = "", floor: float | None = None, agent: str = ""
) -> O.Outcome:
    """ "Is this already filed?" -- the records most like ``text``, before it is added.

    Read-only: the same engine and the same ``[dedupe]`` knobs (``show_floor``,
    ``max_candidates``, ``kinds``) the add-time check uses, asked in the open. Candidates
    cross kinds -- a bug sees the open task that fixes it, a task the bug it would fix --
    and closed records stay in, because a new bug that repeats a fixed one is the case
    worth catching. Each says what it is, where it stands, how close it scored and which
    words it shares, so the match can be judged without opening it. Always runs, whatever
    ``[dedupe].on_match`` says: that setting governs what an ADD does, not whether one may
    look. ``kinds`` narrows to some of ``[dedupe].kinds``. ``floor`` overrides the score
    show floor for this call (None = ``[dedupe].show_floor``), so a near miss below the
    default is still surfaced (B76826fc2be).
    """

    text = (text or "").strip()
    if not text:
        return O.failed(
            "similar",
            "nothing to compare: give the text of the record to be filed",
            candidates=[],
        )
    log, cfg, st = _load(repo, agent)
    allowed = list(cfg.dedupe.kinds)
    want = csv_list(kinds)
    unknown = [k for k in want if k not in allowed]
    if unknown:
        return O.failed(
            "similar",
            f"unknown kind {', '.join(unknown)}: [dedupe].kinds is {', '.join(allowed)}",
            candidates=[],
        )
    scope = want or allowed
    store = _store(repo, log, cfg)
    # The policy engine with the add-time switch forced on and the kinds narrowed.
    dd = dataclasses.replace(cfg.dedupe, on_match="ask", kinds=scope)
    if floor is not None:
        dd = dataclasses.replace(dd, show_floor=float(floor))
    scoped = dataclasses.replace(cfg, dedupe=dd)
    with SC.matcher(store) as matcher:
        found = sim.assess(matcher, {"kind": scope[0], "title": text, "body": ""}, scoped)
        # `assess` lists a record the text NAMES whatever its kind; `kinds` narrows those too.
        cands = [c for c in found.candidates if c.kind in scope]
        rows = DD.rows(st, matcher, cands, text, grace_s=cfg.lease.grace_s)
        note = _semantic_bug_rows(repo, scoped, st, text, rows) if "bug" in scope else ""
    data = {
        "text": text,
        "kinds": scope,
        "show_floor": dd.show_floor,
        "candidates": rows,
        "count": len(rows),
    }
    if "bug" in scope:
        data["semantic"] = note or "semantic + overlap"
    if not rows:
        return O.nothing(
            "similar",
            f"Nothing in {', '.join(scope)} scores {dd.show_floor:g} or more against that text.",
            **data,
        )
    return O.ok("similar", **data)
