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
from .records import ABANDONED, DONE

OPT_OUT = "no-auto-order"
DECIDE_TAGS = frozenset({"decide", "decision"})
ACTOR = "ddflow:auto-order"


@dataclass(frozen=True)
class Cand:
    """What the ordering needs to know of one item."""

    id: str
    state: str = "open"
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
    if c.kind != "task":
        return "not a task"
    if c.state in (DONE, ABANDONED):
        return f"{c.state}"
    if OPT_OUT in c.tags:
        return f"tagged {OPT_OUT}"
    if DECIDE_TAGS & set(c.tags):
        return "a decide task"
    return ""


def _candidates(cycle: Mapping[str, Any]) -> list[tuple[str, str]]:
    """(later, earlier) pairs in the order to try them: the victim's item waits for the
    waiter it blocks; then each waiter for the item it waits behind."""
    links = cycle.get("links", [])
    out: list[tuple[str, str]] = []
    victim_item = cycle.get("victim_item", "")
    for k in links:
        if k.get("blocker") == victim_item or victim_item in k.get("blockers", []):
            out.append((victim_item, k["item"]))
    out += [(k["item"], k["blocker"]) for k in links]
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
        why: list[str] = []
        done = False
        for later, earlier in _candidates(cyc):
            if earlier in reach(later):
                done = True  # already ordered: nothing to add
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
            done = True
            break
        if not done:
            notes.append(f"no edge for {' -> '.join(agents)}: {'; '.join(why) or 'no candidate'}")
    return edges, notes
