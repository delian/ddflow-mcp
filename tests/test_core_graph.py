"""core/graph: the one DAG module (B-uni-graph).

The algorithms are checked against networkx as an oracle on random graphs (hypothesis),
and the walks they replaced -- State.descendants/ancestors, export Query.tasks_under,
progress._waits_on, the board's indent depth -- are pinned against verbatim copies of
the code they replaced, on random parent graphs that include cycles, so the move is
byte-identical (D-unify 4).
"""

from __future__ import annotations

import graphlib
import itertools

import networkx as nx
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from ddflow.core import graph as G
from ddflow.core.model import DONE, Item, State
from ddflow.core.progress import _waits_on
from ddflow.core.schedule import critical_path, find_cycles, inherited_deps
from ddflow.services.export import query
from ddflow.views.markdown import _depth

NODES = [f"n{i}" for i in range(9)]


@st.composite
def digraphs(draw, acyclic: bool = False):
    """A random digraph on up to 9 nodes, as {node: [successor, ...]} (order kept)."""
    n = draw(st.integers(1, len(NODES)))
    nodes = NODES[:n]
    adj: dict[str, list[str]] = {}
    for i, a in enumerate(nodes):
        pool = nodes[i + 1 :] if acyclic else nodes
        adj[a] = draw(st.lists(st.sampled_from(pool), max_size=4)) if pool else []
    return adj


def nx_of(adj: dict[str, list[str]]) -> nx.DiGraph:
    g = nx.DiGraph()
    g.add_nodes_from(adj)
    g.add_edges_from((a, b) for a, bs in adj.items() for b in bs)
    return g


@settings(max_examples=150, deadline=None)
@given(digraphs())
def test_closure_is_networkx_reachability(adj):
    g = nx_of(adj)
    for s in adj:
        got = G.closure(s, lambda n: adj[n])
        assert len(got) == len(set(got))
        on_cycle = any(nx.has_path(g, m, s) for m in adj[s])
        assert set(got) == nx.descendants(g, s) | ({s} if on_cycle else set())


def test_closure_on_a_chain_is_nearest_first_and_iterative():
    deep = {i: [i + 1] for i in range(20_000)}
    deep[20_000] = []
    assert G.closure(0, lambda n: deep[n])[:3] == [1, 2, 3]
    assert len(G.closure(0, lambda n: deep[n])) == 20_000


@settings(max_examples=150, deadline=None)
@given(digraphs())
def test_find_cycles_finds_one_exactly_when_networkx_does(adj):
    items = {k: Item(id=k, kind="task", needs=list(v)) for k, v in adj.items()}
    cycles = G.find_cycles(items)
    assert bool(cycles) != nx.is_directed_acyclic_graph(nx_of(adj))
    for cyc in cycles:
        assert cyc[0] == cyc[-1] == min(cyc)
        assert all(b in adj[a] for a, b in itertools.pairwise(cyc))
    assert find_cycles is G.find_cycles  # the old import path still works


@settings(max_examples=150, deadline=None)
@given(digraphs())
def test_topological_order_puts_every_predecessor_first(adj):
    if not nx.is_directed_acyclic_graph(nx_of(adj)):
        with pytest.raises(graphlib.CycleError):
            G.topological_order(adj, lambda n: adj[n])
        return
    order = G.topological_order(adj, lambda n: adj[n])
    assert sorted(order) == sorted(adj)
    pos = {n: i for i, n in enumerate(order)}
    assert all(pos[b] < pos[a] for a, bs in adj.items() for b in bs)


@settings(max_examples=150, deadline=None)
@given(digraphs(acyclic=True))
def test_longest_chains_match_networkx(adj):
    g = nx_of(adj)
    chains = G.longest_chains(adj, lambda n: adj[n])
    for n, chain in chains.items():
        assert chain[-1] == n
        assert all(b in adj[a] for a, b in zip(chain[1:], chain[:-1], strict=True))
        # the chain runs from the far end of n's longest outgoing adj path to n
        assert len(chain) - 1 == _longest_from(g, n)
    assert max(len(c) for c in chains.values()) == nx.dag_longest_path_length(g) + 1


def test_longest_chains_refuses_a_cycle_instead_of_looping():
    adj = {"a": ["b"], "b": ["c"], "c": ["a"]}
    with pytest.raises(graphlib.CycleError):
        G.longest_chains(adj, lambda n: adj[n])
    with pytest.raises(graphlib.CycleError):
        G.longest_chains(["s"], lambda n: ["s"])


def _longest_from(g: nx.DiGraph, n: str) -> int:
    best = dict.fromkeys(g, 0)
    for m in reversed(list(nx.topological_sort(g))):
        best[m] = max((best[s] + 1 for s in g.successors(m)), default=0)
    return best[n]


@settings(max_examples=150, deadline=None)
@given(digraphs(acyclic=True))
def test_transitive_reduction_matches_networkx(adj):
    red = G.transitive_reduction(adj, lambda n: adj[n])
    got = {(a, b) for a, bs in red.items() for b in bs}
    assert got == set(nx.transitive_reduction(nx_of(adj)).edges())


# -- the walks the module replaced, verbatim, as the pin ----------------------------


def old_descendants(state: State, item_id: str) -> set[str]:
    seen: set[str] = set()
    stack = [item_id]
    while stack:
        node = stack.pop()
        for child in state.children(node):
            if child.id in seen:
                continue
            seen.add(child.id)
            stack.append(child.id)
    return seen


def old_ancestors(state: State, item_id: str) -> list[str]:
    out: list[str] = []
    seen = {item_id}
    node = state.items.get(item_id)
    while node is not None and node.parent and node.parent not in seen:
        seen.add(node.parent)
        node = state.items.get(node.parent)
        if node is None or node.removed:
            break
        out.append(node.id)
    return out


def old_depth(state: State, item, root: str) -> int:
    depth, node, seen = 0, item, set()
    while node.parent and node.parent != root and node.parent not in seen:
        seen.add(node.id)
        node = state.items.get(node.parent)
        if node is None:
            break
        depth += 1
    return min(depth, 6)


def old_tasks_under(q, parent: str) -> list[str]:
    out = []
    stack = [parent]
    seen = {parent}
    while stack:
        for c in q.children.get(stack.pop(), ()):
            if c.id in seen:
                continue
            seen.add(c.id)
            if c.kind == "task":
                out.append(c)
            stack.append(c.id)
    return [t.id for t in sorted(out, key=query.item_key)]


def old_waits_on(state: State, item_id: str) -> set[str]:
    def live(i: str) -> bool:
        it = state.items.get(i)
        return bool(it and not it.removed and it.state not in (DONE, "abandoned"))

    seen: set[str] = set()
    stack = [item_id]
    while stack:
        it = state.items[stack.pop()]
        for _owner, dep in inherited_deps(state, it):
            if not live(dep):
                continue
            for n in (dep, *sorted(state.descendants(dep))):
                if n not in seen and live(n):
                    seen.add(n)
                    stack.append(n)
    return seen


@st.composite
def states(draw):
    """Random items: any parent (cycles and self-parents included), needs, removals,
    kinds and states -- the shapes an operator-authored plan can take when it is wrong."""
    n = draw(st.integers(1, 8))
    ids = NODES[:n]
    s = State()
    for i in ids:
        s.items[i] = Item(
            id=i,
            kind=draw(st.sampled_from(["task", "task", "phase"])),
            parent=draw(st.sampled_from(["", "", *ids, "ghost"])),
            needs=draw(st.lists(st.sampled_from([*ids, "ghost"]), max_size=3)),
            removed=draw(st.booleans()) and draw(st.booleans()),
            state=draw(st.sampled_from(["open", "open", DONE, "running", "abandoned"])),
        )
    return s


@settings(max_examples=300, deadline=None)
@given(states())
def test_moved_walks_are_byte_identical(s):
    q = query.Query(s, [])
    for i, it in s.items.items():
        assert s.descendants(i) == old_descendants(s, i)
        assert [a.id for a in s.ancestors(i)] == old_ancestors(s, i)
        for root in ("", *s.items):
            assert _depth(s, it, root) == old_depth(s, it, root)
        assert [t.id for t in q.tasks_under(i)] == old_tasks_under(q, i)
        assert _waits_on(s, i) == old_waits_on(s, i)


def test_self_parent_keeps_its_one_step_of_indent():
    s = State()
    s.items["A"] = Item(id="A", kind="task", parent="A")
    assert _depth(s, s.items["A"], "") == 1
    assert s.ancestors("A") == []
    assert s.descendants("A") == {"A"}


def test_critical_path_unchanged_on_a_known_plan():
    s = State()
    s.items["P"] = Item(id="P", kind="phase")
    s.items["Q"] = Item(id="Q", kind="phase", needs=["P"])
    for t, parent, needs in [
        ("a", "P", []),
        ("b", "P", ["a"]),
        ("c", "P", ["b"]),
        ("d", "Q", []),
        ("e", "Q", ["d"]),
        ("x", "P", []),
    ]:
        s.items[t] = Item(id=t, kind="task", parent=parent, needs=needs)
    assert critical_path(s) == ["a", "b", "c", "P", "d", "e"]
    assert critical_path(s, "P") == ["a", "b", "c"]
    s.items["a"].needs = ["c"]  # a cycle: refused, not a confident wrong number
    assert critical_path(s) == []


def old_critical_path(state: State, phase: str = "") -> list[str]:
    def before(it: Item) -> list[str]:
        deps = [d for _owner, d in inherited_deps(state, it)]
        return deps + [c.id for c in state.children(it.id) if not c.removed]

    live = {i.id: i for i in state.items.values() if not i.removed}
    if phase:
        inside, todo = set(), [phase]
        while todo:
            n = todo.pop()
            if n in inside or n not in live:
                continue
            inside.add(n)
            todo += before(live[n])
        live = {k: v for k, v in live.items() if k in inside}
    items = live
    if find_cycles(items, edges=before):
        return []
    memo: dict[str, list[str]] = {}

    def longest(n: str, seen: frozenset[str]) -> list[str]:
        if n in seen:
            return []
        if n in memo:
            return memo[n]
        it = items.get(n)
        best: list[str] = []
        if it:
            for dep in before(it):
                if dep in items and items[dep].state != DONE:
                    cand = longest(dep, seen | {n})
                    if len(cand) > len(best):
                        best = cand
        memo[n] = [*best, n]
        return memo[n]

    def trimmed(chain: list[str]) -> list[str]:
        while chain and items[chain[-1]].kind != "task":
            chain = chain[:-1]
        return chain

    chains = [trimmed(longest(i, frozenset())) for i, it in items.items() if it.state != DONE]
    return max(chains, key=len) if chains else []


@settings(max_examples=300, deadline=None)
@given(states())
def test_critical_path_is_byte_identical(s):
    for phase in ("", *s.items, "ghost"):
        assert critical_path(s, phase) == old_critical_path(s, phase)
