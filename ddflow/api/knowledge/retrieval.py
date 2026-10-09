"""Finding what the project already knows: `recall` and `similar`."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import ddflow.api._dedupe as DD

from ...config import csv_list
from ...core import outcome as O
from ...core.budget import RECALL_MAX_CHARS, Budget
from ...infra.store import RECALL_SOURCES
from ...services import contextpack as CP
from .._base import _load
from .lessons import _store


def _wire_hit(table: str, label: str, r: dict) -> dict:
    """One hit as the `--json` / MCP body carries it (see `contextpack.candidate`)."""
    return CP.candidate(table, label, r).hit


def _origin(st, table: str, ident):
    """The `Origin` of a recalled decision, lesson or memory; None for the other kinds."""
    from ...core import provenance as PV

    if table == "decisions" and ident in st.decisions:
        return PV.decision_origin(st.decisions[ident])
    if table == "lessons" and ident in st.lessons:
        return PV.lesson_origin(st.lessons[ident])
    if table == "memories" and ident in st.memories:
        return PV.memory_origin(st.memories[ident])
    return None


def _search_sources(store, query: str, sources: str, limit: int) -> dict[str, list[dict]]:
    """The hits of every wanted source, in rank order, by table; a source with none is absent."""
    want = csv_list(sources) or [t for t, _, _ in RECALL_SOURCES]
    lowered = [w.lower() for w in want]
    results: dict[str, list[dict]] = {}
    for table, label, _why in RECALL_SOURCES:
        if table not in want and label.lower() not in lowered:
            continue
        try:
            hits = store.search(table, query, limit)
        except Exception:
            # One unreadable source must not take the whole recall down: the value is in
            # the union, and "the lessons table is corrupt" is not a reason to withhold
            # the decisions.
            hits = []
        if hits:
            results[table] = hits
    return results


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
    results = _search_sources(_store(repo, log, cfg), query, sources, limit)
    labels = {table: label for table, label, _ in RECALL_SOURCES}
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
        "searched": [t for t, _, _ in RECALL_SOURCES],
        "max_chars": max_chars,
        "pack": {
            "shown": cut.shown,
            "total": cut.total,
            "duplicates": cut.duplicates,
            "truncated": cut.truncated,
            "note": cut.note(),
            "cited": list(cut.cited),
        },
        "_render": {"results": results, "sources": RECALL_SOURCES},
    }
    if not results:
        return O.nothing(
            "recall",
            f"Nothing recalled for {query!r}.\n"
            f"Searched: {', '.join(t for t, _, _ in RECALL_SOURCES)}.",
            **data,
        )
    # What the budget or the duplicate check left out is said where a caller reads it: the
    # MCP result's second block. The CLI prints its own line under the records.
    return O.Outcome(kind="recall", data=data, reason=cut.note())


def similar(repo: Path, text: str, *, kinds: str = "", agent: str = "") -> O.Outcome:
    """ "Is this already filed?" -- the records most like ``text``, before it is added.

    Read-only: the same engine and the same ``[dedupe]`` knobs (``show_floor``,
    ``max_candidates``, ``kinds``) the add-time check uses, asked in the open. Candidates
    cross kinds -- a bug sees the open task that fixes it, a task the bug it would fix --
    and closed records stay in, because a new bug that repeats a fixed one is the case
    worth catching. Each says what it is, where it stands, how close it scored and which
    words it shares, so the match can be judged without opening it. Always runs, whatever
    ``[dedupe].on_match`` says: that setting governs what an ADD does, not whether one may
    look. ``kinds`` narrows to some of ``[dedupe].kinds``.
    """
    import dataclasses

    from ...services import similar as sim

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
    scoped = dataclasses.replace(cfg, dedupe=dd)
    with sim.open_store(store) as matcher:
        found = sim.assess(matcher, {"kind": scope[0], "title": text, "body": ""}, scoped)
        # `assess` lists a record the text NAMES whatever its kind; `kinds` narrows those too.
        cands = [c for c in found.candidates if c.kind in scope]
        rows = DD.rows(st, matcher, cands, text, grace_s=cfg.lease.grace_s)
    data = {
        "text": text,
        "kinds": scope,
        "show_floor": cfg.dedupe.show_floor,
        "candidates": rows,
        "count": len(rows),
    }
    if not rows:
        return O.nothing(
            "similar",
            f"Nothing in {', '.join(scope)} scores {cfg.dedupe.show_floor:g} or more "
            "against that text.",
            **data,
        )
    return O.ok("similar", **data)
