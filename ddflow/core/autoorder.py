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

_ADDED, _ALREADY, _SKIP = "added", "already", "skip"


def _take(
    later: str,
    earlier: str,
    items: Mapping[str, Cand],
    needs: dict[str, list[str]],
    out: list[Edge],
    why: list[str],
    agents: tuple[str, ...],
    files: tuple[str, ...],
) -> str:
    """Try one ``(later, earlier)`` edge: ``_ADDED`` it, found it ``_ALREADY`` (implied), or
    ``_SKIP``ped it (in ``why``)."""
    if later == earlier:
        return _SKIP
    if earlier in closure(later, lambda n: needs.get(n, [])):
        return _ALREADY
    if later in closure(earlier, lambda n: needs.get(n, [])):
        why.append(f"{later}/{earlier}: would close a needs cycle")
        return _SKIP
    bad = eligible(items.get(later)) or eligible(items.get(earlier))
    if bad:
        why.append(f"{later}/{earlier}: {bad}")
        return _SKIP
    out.append(Edge(later, earlier, agents, files))
    needs.setdefault(later, []).append(earlier)
    return _ADDED


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


def propose(
    cycles: list[Mapping[str, Any]], items: Mapping[str, Cand]
) -> tuple[list[Edge], list[str]]:
    """``(edges, notes)``: the edges to add, in cycle order, and why a cycle got none."""
    needs = {i: list(c.needs) for i, c in items.items()}
    edges: list[Edge] = []
    notes: list[str] = []

    for cyc in cycles:
        agents = tuple(cyc.get("agents", []))
        links = cyc.get("links", [])
        files = tuple(cyc.get("files", []))
        why: list[str] = []
        added = 0
        already = False
        # Every dropped lease of the victim must yield: one edge each, or the next can take
        # the files straight back.
        drew = [(v, k["item"]) for v in _victim_items(cyc) for k in links if v in _blockers(k)]
        for later, earlier in drew:
            got = _take(later, earlier, items, needs, edges, why, agents, files)
            added += got == _ADDED
            already = already or got == _ALREADY
        # No drop could be ordered: one fallback edge (the waiter waits for a blocker).
        if added == 0 and not already:
            for later, earlier in [(k["item"], b) for k in links for b in _blockers(k)]:
                got = _take(later, earlier, items, needs, edges, why, agents, files)
                if got == _ALREADY:
                    already = True
                    break
                if got == _ADDED:
                    added = 1  # one fallback edge is enough
                    break
        if added == 0 and not already:
            notes.append(f"no edge for {' -> '.join(agents)}: {'; '.join(why) or 'no candidate'}")
    return edges, notes
