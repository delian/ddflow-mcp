"""Order colliding items by `needs` so a lease deadlock cannot recur (DX-conflict-autoorder).

Pure: given the cycles `leasewait.find` reports (as `Cycle.as_dict()`) and a plain view of
the items, ``propose`` says which `needs` edges to add. One edge per cycle is enough: the
victim's item waits for the item of the agent that was waiting on it, so the victim
yields the contested files and cannot take them back until the other item is done.

An edge is never proposed when it is already implied (one item already reaches the other
through `needs`), when it would close a `needs` cycle, or when either end is settled
(done, abandoned), a decide task (nothing waits on a decision, L-needs-on-decide-tasks),
not a task, or tagged `no-auto-order`. The next candidate of the same cycle is tried, the
victim's first, so the result is deterministic and minimal.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .graph import closure
from .records import WORK_KINDS

OPT_OUT = "no-auto-order"
DECIDE_TAGS = frozenset({"decide", "decision"})
ACTOR = "ddflow:auto-order"


def is_decide(tags) -> bool:
    """Does a tag list mark a decide task (case and padding ignored)?"""
    return bool(DECIDE_TAGS & {str(t).strip().lower() for t in tags or ()})


@dataclass(frozen=True)
class Cand:
    """What the ordering needs to know of one item."""

    id: str
    terminal: bool = False  # done or abandoned
    kind: str = "task"
    tags: tuple[str, ...] = ()
    needs: tuple[str, ...] = ()


@dataclass(frozen=True)
class Edge:
    """``later`` is to wait for ``earlier``; ``agents`` is the cycle it breaks."""

    later: str
    earlier: str
    agents: tuple[str, ...]
    files: tuple[str, ...]

    @property
    def reason(self) -> str:
        loop = " -> ".join([*self.agents, self.agents[0]]) if self.agents else "-"
        return (
            f"lease deadlock {loop} over {', '.join(self.files) or '-'}: "
            f"{self.later} now waits for {self.earlier}"
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "later": self.later,
            "earlier": self.earlier,
            "agents": list(self.agents),
            "files": list(self.files),
            "reason": self.reason,
        }


def eligible(c: Cand | None) -> str:
    """Why ``c`` may not be ordered ('' when it may)."""
    if c is None:
        return "unknown item"
    if c.kind not in WORK_KINDS:
        return "not a task"
    if c.terminal:
        return "settled"
    if OPT_OUT in c.tags:
        return f"tagged {OPT_OUT}"
    if DECIDE_TAGS & set(c.tags):
        return "a decide task"
    return ""


def _blockers(k: Mapping[str, Any]) -> list[str]:
    return list(
        dict.fromkeys([*([k["blocker"]] if k.get("blocker") else []), *k.get("blockers", [])])
    )


def _victim_items(cycle: Mapping[str, Any]) -> list[str]:
    """Every lease the victim must drop to end this cycle, in deterministic order.

    ``cycle['victim_item']`` is only the first drop; the link whose blockers name it is the
    waiter the victim yields to, and that link's blockers are all of the victim's items in
    the way. Each must be ordered to yield, or the rest can take the files straight back."""
    victim_item = cycle.get("victim_item", "")
    for k in cycle.get("links", []):
        if victim_item and victim_item in _blockers(k):
            return _blockers(k)
    return [victim_item] if victim_item else []


def _candidates(cycle: Mapping[str, Any]) -> list[tuple[str, str]]:
    """(later, earlier) pairs in the order to try them: each item the victim drops waits for
    the waiter it blocks; then each waiter for the items it waits behind."""
    links = cycle.get("links", [])
    out: list[tuple[str, str]] = []
    out += [(v, k["item"]) for v in _victim_items(cycle) for k in links if v in _blockers(k)]
    out += [(k["item"], b) for k in links for b in _blockers(k)]
    return list(dict.fromkeys(p for p in out if p[0] != p[1]))


def propose(
    cycles: list[Mapping[str, Any]], items: Mapping[str, Cand]
) -> tuple[list[Edge], list[str]]:
    """``(edges, notes)``: the edges to add, in cycle order, and why a cycle got none."""
    needs = {i: list(c.needs) for i, c in items.items()}
    edges: list[Edge] = []
    notes: list[str] = []

    def reach(a: str) -> list[str]:
        return closure(a, lambda n: needs.get(n, []))

    for cyc in cycles:
        agents = tuple(cyc.get("agents", []))
        links = cyc.get("links", [])
        victim_edges = [(v, k["item"]) for v in _victim_items(cyc) for k in links if v in _blockers(k)]
        fallbacks = [(k["item"], b) for k in links for b in _blockers(k)]
        why: list[str] = []
        added = 0
        already = False
        # every dropped lease of the victim must yield: one edge each, or the next can take
        # the files straight back.
        for later, earlier in victim_edges:
            if earlier in reach(later):
                already = True  # this drop is already ordered: nothing for this one
                continue
            if later in reach(earlier):
                why.append(f"{later}/{earlier}: would close a needs cycle")
                continue
            bad = eligible(items.get(later)) or eligible(items.get(earlier))
            if bad:
                why.append(f"{later}/{earlier}: {bad}")
                continue
            edges.append(Edge(later, earlier, agents, tuple(cyc.get("files", []))))
            needs.setdefault(later, []).append(earlier)
            added += 1
        # no drop could be ordered: one fallback edge (the waiter waits for a blocker).
        if added == 0:
            for later, earlier in fallbacks:
                if later == earlier:
                    continue
                if earlier in reach(later):
                    already = True
                    break
                if later in reach(earlier):
                    why.append(f"{later}/{earlier}: would close a needs cycle")
                    continue
                bad = eligible(items.get(later)) or eligible(items.get(earlier))
                if bad:
                    why.append(f"{later}/{earlier}: {bad}")
                    continue
                edges.append(Edge(later, earlier, agents, tuple(cyc.get("files", []))))
                needs.setdefault(later, []).append(earlier)
                break  # one fallback edge is enough
        if added == 0 and not already:
            notes.append(f"no edge for {' -> '.join(agents)}: {'; '.join(why) or 'no candidate'}")
    return edges, notes
