"""Apply (or only report) the `needs` edges that end a lease deadlock (DX-conflict-autoorder).

``[schedule].auto_order``: ``off`` does nothing, ``propose`` reports the edges, ``apply``
records each as a `task.updated` event carrying ``auto_order`` (the reason and the cycle)
under the actor `ddflow:auto-order`. Applying is idempotent -- an edge already in the
item's `needs` is not proposed again -- and reversible: `ddflow update <id> --needs ...`
drops it. Every edge is re-checked against `graph.find_cycles` before it is written.
"""

from __future__ import annotations

from typing import Any

from ..core import autoorder as AO
from ..core.graph import find_cycles

MODES = ("off", "propose", "apply")


def view(st) -> dict[str, AO.Cand]:
    return {
        i: AO.Cand(i, it.terminal, it.kind, tuple(it.tags), tuple(it.needs))
        for i, it in st.items.items()
        if not getattr(it, "removed", False)
    }


def plan(st, cycles: list[dict[str, Any]]) -> tuple[list[AO.Edge], list[str]]:
    return AO.propose(cycles, view(st))


def run(log, st, cfg, cycles: list[dict[str, Any]], mode: str = "") -> dict[str, Any]:
    """``{"mode", "edges": [...], "applied": [...], "notes": [...]}``; writes only in apply."""
    mode = mode or cfg.schedule.auto_order
    if mode not in MODES or mode == "off" or not cycles:
        return {"mode": mode, "edges": [], "applied": [], "notes": []}
    edges, notes = plan(st, cycles)
    applied: list[dict[str, Any]] = []
    if mode == "apply":
        needs = {i: list(c.needs) for i, c in view(st).items()}
        for e in edges:
            trial = {**needs, e.later: [*needs.get(e.later, []), e.earlier]}
            if any(e.later in c for c in find_cycles(trial, lambda n: n)):
                notes.append(f"refused {e.later} -> {e.earlier}: closes a needs cycle")
                continue
            log.append(
                "task.updated",
                e.later,
                {"needs": trial[e.later], "auto_order": {**e.as_dict(), "by": AO.ACTOR}},
            )
            needs = trial
            applied.append(e.as_dict())
    return {"mode": mode, "edges": [e.as_dict() for e in edges], "applied": applied, "notes": notes}


def auto_edges(events, st) -> list[dict[str, Any]]:
    """The auto-order edges still standing: recorded, and still in the item's `needs`."""
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for e in events:
        a = e.kind == "task.updated" and e.data.get("auto_order")
        if a and a.get("earlier") in getattr(st.items.get(e.subject), "needs", ()):
            out[(e.subject, a["earlier"])] = a
    return list(out.values())


def lines(res: dict[str, Any]) -> list[str]:
    if res["mode"] == "apply":
        done = [f"auto-order added: {e['reason']}" for e in res["applied"]]
    else:
        done = [f"auto-order would add (mode {res['mode']}): {e['reason']}" for e in res["edges"]]
    return done + [f"auto-order: {n}" for n in res["notes"]]
