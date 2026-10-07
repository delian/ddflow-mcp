"""One home for walking a directed graph: reachability, cycles, order, longest chain.

Every graph ddflow walks -- the parent tree, `needs`, inherited dependencies, job
`needs` -- is operator-authored. So it can be deep (no recursion here: depth is the
length of a chain nobody bounded) and it can be wrong (every walk is cycle-guarded,
because the code that walks it is also the code that diagnoses bad plans).

A graph is given as a node set plus an ``edges(node) -> iterable of nodes`` function,
so a caller keeps its own notion of what an edge is (a child, a dependency, an
inherited dependency) and of which nodes are live; nothing here knows what an item is.
An edge to a node the caller did not list is the caller's business: ``closure`` follows
whatever ``edges`` returns, the other functions ignore edges leaving the node set.
"""

from __future__ import annotations

from collections.abc import Callable, Hashable, Iterable, Mapping
from graphlib import TopologicalSorter
from typing import Any, TypeVar

N = TypeVar("N", bound=Hashable)
T = TypeVar("T")


def closure(start: N, edges: Callable[[N], Iterable[N]]) -> list[N]:
    """Every node reachable from ``start`` by one or more edges, in discovery order.

    ``start`` itself is in the result only when a cycle leads back to it. Depth-first,
    iterative, each node once: on a chain (one edge per node) the order is nearest
    first, which is what a parent chain wants.
    """
    seen: set[N] = set()
    out: list[N] = []
    stack = [start]
    while stack:
        for nxt in edges(stack.pop()):
            if nxt in seen:
                continue
            seen.add(nxt)
            out.append(nxt)
            stack.append(nxt)
    return out


def find_cycles(
    items: Mapping[str, T], edges: Callable[[T], list[str]] | None = None
) -> list[list[str]]:
    """Every dependency cycle, each reported once, starting at its smallest id.

    Iterative DFS. Recursion depth here is the length of the dependency chain, which is
    operator-authored and therefore unbounded by anything the code controls; a plan with
    a thousand-deep chain would raise RecursionError inside the health check that exists
    to diagnose bad plans.

    `edges` says which graph to walk, and a caller must pass the one it will walk
    itself. `critical_path` traverses INHERITED dependencies while this defaulted to
    direct `needs`, so a cycle existing only in the inherited graph passed the guard and
    the memoised longest-path returned a confidently wrong number -- the exact outcome
    the guard exists to refuse.
    """
    edge: Callable[[Any], list[str]] = edges or (lambda it: list(it.needs))
    WHITE, GREY, BLACK = 0, 1, 2
    colour: dict[str, int] = {}
    found: list[list[str]] = []

    for root in sorted(items):
        if colour.get(root, WHITE) != WHITE:
            continue
        stack: list[tuple[str, list[str]]] = [(root, edge(items[root]))]
        path: list[str] = [root]
        colour[root] = GREY
        while stack:
            _node, pending = stack[-1]
            if not pending:
                stack.pop()
                colour[path.pop()] = BLACK
                continue
            dep = pending.pop()
            if dep not in items:
                continue
            c = colour.get(dep, WHITE)
            if c == GREY:
                cyc = path[path.index(dep) :]
                lo = cyc.index(min(cyc))
                found.append(cyc[lo:] + cyc[:lo] + [cyc[lo]])
            elif c == WHITE:
                colour[dep] = GREY
                path.append(dep)
                stack.append((dep, edge(items[dep])))
    uniq = {tuple(c): c for c in found}
    return sorted(uniq.values())


def topological_order(nodes: Iterable[N], before: Callable[[N], Iterable[N]]) -> list[N]:
    """``nodes`` ordered so that everything in ``before(n)`` comes ahead of ``n``.

    stdlib ``graphlib``; edges leaving ``nodes`` are ignored. A cycle raises
    ``graphlib.CycleError`` -- ask ``find_cycles`` first where a cycle is possible.
    """
    node_list = list(nodes)
    members = set(node_list)
    ts: TopologicalSorter[N] = TopologicalSorter()
    for n in node_list:
        ts.add(n, *(p for p in before(n) if p in members))
    return list(ts.static_order())


def longest_chains(nodes: Iterable[N], before: Callable[[N], Iterable[N]]) -> dict[N, list[N]]:
    """For every node, the longest chain of ``before`` edges ending at it (itself last).

    The graph must be acyclic (``find_cycles`` first); edges leaving ``nodes`` are
    ignored. Ties go to the first strictly longer candidate in ``before``'s order, so
    the answer is deterministic for a deterministic ``before``. Iterative post-order:
    a chain is as deep as the plan, not as the interpreter's stack.
    """
    node_list = list(nodes)
    members = set(node_list)
    memo: dict[N, list[N]] = {}
    for root in node_list:
        if root in memo:
            continue
        stack: list[tuple[N, list[N], int]] = [(root, [p for p in before(root) if p in members], 0)]
        while stack:
            n, preds, i = stack[-1]
            if i < len(preds):
                stack[-1] = (n, preds, i + 1)
                p = preds[i]
                if p not in memo:
                    stack.append((p, [q for q in before(p) if q in members], 0))
                continue
            stack.pop()
            best: list[N] = []
            for p in preds:
                if len(memo[p]) > len(best):
                    best = memo[p]
            memo[n] = [*best, n]
    return memo


def transitive_reduction(nodes: Iterable[N], edges: Callable[[N], Iterable[N]]) -> dict[N, list[N]]:
    """The fewest edges with the same reachability: ``u -> v`` is dropped when ``v`` is
    reachable from ``u`` some other way. Acyclic graphs only; edges leaving ``nodes``
    are ignored. Each node's kept edges stay in ``edges``' order, without repeats."""
    node_list = list(nodes)
    members = set(node_list)
    succ = {n: list(dict.fromkeys(m for m in edges(n) if m in members)) for n in node_list}
    reach: dict[N, set[N]] = {}
    # topological_order puts ``before`` first; with succ as ``before``, successors come
    # first, so iterating it in order computes every successor's reach before its own.
    for n in topological_order(node_list, lambda x: succ[x]):
        r: set[N] = set()
        for m in succ[n]:
            r.add(m)
            r |= reach[m]
        reach[n] = r
    out: dict[N, list[N]] = {}
    for n in node_list:
        indirect: set[N] = set()
        for m in succ[n]:
            indirect |= reach[m]
        out[n] = [m for m in succ[n] if m not in indirect]
    return out
