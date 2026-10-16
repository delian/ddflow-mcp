"""Cycles of agents waiting on each other's leases: a lock deadlock, as data.

Three agents once waited 45 minutes on each other with every lease current and nothing
running. `services.waiting` records who waits for whom; this module is the pure half that
finds the closed loops in that wait-for graph. It takes plain values (no state, no clock)
so the doctor report, the lanes advisor and the auto-order task all read one answer.

The graph's nodes are AGENTS: an agent that waits on item A while holding item B is
blocked by whoever holds A, whatever item it is waiting FROM. A wait on one's own lease
(a self-wait) is a cycle of one: nothing can ever release it.

A cycle carries a VICTIM, the waiter that has waited least (then the lexically last
agent, so the choice is deterministic), and the remedy for it: drop from the victim's
lease the globs the cycle's other waiter asked for. Nothing is taken from anyone.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import pairwise
from typing import Any

from .globs import overlap
from .graph import find_cycles


@dataclass(frozen=True)
class Wait:
    """One standing wait: ``agent`` asked, on ``item``, for ``globs``; ``on`` lists the
    ``(item, holder)`` leases in its way."""

    item: str
    agent: str
    since: float
    globs: tuple[str, ...]
    on: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class Held:
    """A live lease: who holds ``item`` and which globs."""

    item: str
    holder: str
    globs: tuple[str, ...]


@dataclass(frozen=True)
class Link:
    """One edge of a cycle: ``agent`` (on ``item``) waits for ``holder`` (on ``blocker``)
    over the ``files`` of the blocker's lease that its request overlaps."""

    agent: str
    item: str
    holder: str
    blocker: str
    files: tuple[str, ...]
    since: float


@dataclass(frozen=True)
class Cycle:
    """A closed loop of waits. ``agents`` and ``items`` are in cycle order, from the
    smallest agent; ``victim`` is the agent asked to yield and ``release`` the globs of its
    lease that break the loop (empty when it holds none of the contested ones)."""

    agents: tuple[str, ...]
    items: tuple[str, ...]
    files: tuple[str, ...]
    links: tuple[Link, ...]
    victim: str
    victim_item: str
    release: tuple[str, ...]
    keep: tuple[str, ...] = field(default=())
    remedy: str = ""

    def as_dict(self) -> dict[str, Any]:
        """The structured form: what a follow-on that adds `needs` edges reads."""
        return {
            "agents": list(self.agents),
            "items": list(self.items),
            "files": list(self.files),
            "victim": self.victim,
            "victim_item": self.victim_item,
            "release": list(self.release),
            "keep": list(self.keep),
            "remedy": self.remedy,
            "links": [
                {
                    "agent": k.agent,
                    "item": k.item,
                    "holder": k.holder,
                    "blocker": k.blocker,
                    "files": list(k.files),
                    "since": k.since,
                }
                for k in self.links
            ],
        }

    def render(self) -> str:
        loop = " -> ".join([*self.agents, self.agents[0]])
        what = ", ".join(sorted(set(self.items))) or "-"
        files = ", ".join(self.files) or "-"
        return f"lease deadlock: {loop} (items {what}; files {files}). {self.remedy}"


def _contested(lease: Held | None, asked: tuple[str, ...]) -> tuple[str, ...]:
    if lease is None:
        return ()
    return tuple(sorted({g for g in lease.globs if any(overlap(g, a) for a in asked)}))


def _remedy(victim: Link, prev: Link, wanted: tuple[str, ...], held: Held | None) -> tuple:
    """(release, keep, text) for ``victim``, who must give up ``wanted``: the globs of the
    lease ``prev`` (the waiter for the victim's lease) is blocked on."""
    then = (
        f"then {prev.agent} retries on {prev.item}, and {victim.agent} queues behind it "
        f"(`ddflow wait --item {victim.blocker}`)"
    )
    if held is None or not wanted:
        return (), (), f"{victim.agent} should stop waiting and report to the coordinator; {then}."
    keep = tuple(g for g in held.globs if g not in wanted)
    how = (
        f"`ddflow update {held.item} --globs {','.join(keep)}`"
        if keep
        else f"`ddflow release {held.item}`"
    )
    return wanted, keep, f"{victim.agent} yields {', '.join(wanted)}: {how}; {then}."


def _edges(waits: list[Wait], held: dict[str, Held]) -> dict[str, dict[str, Link]]:
    """agent -> blocking agent -> the oldest Link by which it waits there."""
    out: dict[str, dict[str, Link]] = {}
    for w in waits:
        for blocker, holder in w.on:
            lz = held.get(blocker)
            if lz is None or lz.holder != holder:
                continue  # let go (or taken by another) since the record was made
            link = Link(w.agent, w.item, holder, blocker, _contested(lz, w.globs), w.since)
            row = out.setdefault(w.agent, {})
            if holder not in row or link.since < row[holder].since:
                row[holder] = link
    return out


def _cycle(links: list[Link], waits: list[Wait], leases: list[Held]) -> Cycle:
    """The report for one ring of ``links``: victim, what it releases, the remedy."""
    # Least progress first: the wait that is youngest, then the later agent id.
    vi = max(range(len(links)), key=lambda i: (links[i].since, links[i].agent))
    victim = links[vi]
    prev = links[vi - 1]  # whoever waits for the victim's lease, and the lease it waits on
    own = next((lz for lz in leases if lz.item == prev.blocker), None)
    wanted = _contested(own, _asked(waits, prev))
    release, keep, text = _remedy(victim, prev, wanted, own)
    return Cycle(
        agents=tuple(lk.agent for lk in links),
        items=tuple(dict.fromkeys(i for lk in links for i in (lk.item, lk.blocker))),
        files=tuple(sorted({f for lk in links for f in lk.files})),
        links=tuple(links),
        victim=victim.agent,
        victim_item=own.item if own else victim.item,
        release=release,
        keep=keep,
        remedy=text,
    )


def find(waits: list[Wait], leases: list[Held]) -> list[Cycle]:
    """Every wait-for cycle among ``waits`` given the live ``leases``, ordered by their
    smallest agent. A chain that does not close is not a cycle; a wait whose blockers
    have let go (its ``on`` names no live lease) contributes no edge."""
    out = _edges(waits, {lz.item: lz for lz in leases})
    nodes = {a: a for a in out} | {h: h for row in out.values() for h in row}
    rings = find_cycles(nodes, lambda a: sorted(out.get(a, {}), reverse=True))
    return sorted(
        (_cycle([out[a][b] for a, b in pairwise(r)], waits, leases) for r in rings),
        key=lambda c: c.agents,
    )


def _asked(waits: list[Wait], link: Link) -> tuple[str, ...]:
    """The globs ``link``'s waiter asked for."""
    return next((w.globs for w in waits if w.agent == link.agent and w.item == link.item), ())
