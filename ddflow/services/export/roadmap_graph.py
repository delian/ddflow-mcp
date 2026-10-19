"""The roadmap's phase dependency graph (B-rm-graph): edges reduced, nodes capped, as Mermaid.

Pure over plain ids: ``graph`` takes the phases in order with their lane, who waits on whom,
and the cap; the template draws the result as a `flowchart LR` (GitHub renders Mermaid).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping


def node_key(pid: str) -> str:
    """A Mermaid-safe node name for a phase id."""
    return "n_" + re.sub(r"\W", "_", pid)


def reduce_edges(
    ids: Iterable[str], before: Callable[[str], Iterable[str]]
) -> list[tuple[str, str]]:
    """``(prerequisite, dependent)`` pairs among ``ids`` with every edge a longer path already
    implies removed. A cycle's edges are kept as they are (no path to compare against)."""
    members = list(ids)
    inset = set(members)
    pre = {i: {d for d in before(i) if d in inset and d != i} for i in members}

    def reaches(start: str, target: str, avoid: str) -> bool:
        """Is ``target`` a prerequisite of ``start``, by a path that never goes through ``avoid``?"""
        seen, todo = {avoid}, [start]
        while todo:
            n = todo.pop()
            if n == target:
                return True
            if n not in seen:
                seen.add(n)
                todo.extend(pre[n])
        return False

    return [
        (d, i)
        for i in members
        for d in sorted(pre[i])
        if not any(reaches(o, d, i) for o in pre[i] if o != d)
    ]


def graph(
    phases: list[Mapping[str, str]], before: Callable[[str], Iterable[str]], cap: int
) -> dict:
    """``{nodes: [{id, key, lane}], edges: [[key, key]], more: N}`` for the first ``cap`` phases
    (``cap`` <= 0 shows none); ``more`` counts the phases left off. Edges are reduced among the
    shown phases only, so a link through a phase that was cut stays drawn."""
    shown = phases[: max(cap, 0)]
    ids = [p["id"] for p in shown]
    return {
        "nodes": [{"id": p["id"], "key": node_key(p["id"]), "lane": p["lane"]} for p in shown],
        "edges": [[node_key(a), node_key(b)] for a, b in reduce_edges(ids, before)],
        "more": len(phases) - len(shown),
    }
