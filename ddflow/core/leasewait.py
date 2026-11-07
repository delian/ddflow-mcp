"""Cycles of agents waiting on each other's leases: a lock deadlock, as data.

Three agents once waited 45 minutes on each other with every lease current and nothing
running. `services.waiting` records who waits for whom; this module is the pure half that
finds the closed loops in that wait-for graph. It takes plain values (no state, no clock)
so the doctor report, the lanes advisor and the auto-order task all read one answer.

The graph's nodes are AGENTS: an agent that waits on item A while holding item B is
blocked by whoever holds A, whatever item it is waiting FROM. A wait on one's own lease
(a self-wait) is a cycle of one: nothing can ever release it.

A cycle carries a VICTIM, the holder with the least progress on the leases the ring waits
for (then the waiter that has waited least, then the lexically last agent, so the choice is
deterministic), and the remedy for it: drop from the victim's leases the globs the cycle's
other waiter asked for. Nothing is taken from anyone.

A wait need not close a loop to be stuck: an ALARM is a wait older than a limit behind leases
none of which shows work for that long, the case no cycle report can name.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from itertools import pairwise
from shlex import quote
from typing import Any

from . import clock
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
    #: epoch seconds of the newest work on it; 0.0 = not measured
    progress_at: float = 0.0


@dataclass(frozen=True)
class Drop:
    """What one lease gives up: ``release`` goes, ``keep`` stays (empty: the whole lease)."""

    item: str
    release: tuple[str, ...]
    keep: tuple[str, ...]

    def command(self) -> str:
        """The line to run. An id with a leading hyphen goes after `--`, or the parser takes
        it for an option (`quote` leaves it as it is)."""
        item, globs = quote(self.item), quote(",".join(self.keep))
        if self.item.startswith("-"):
            return (
                f"ddflow update --globs {globs} -- {item}"
                if self.keep
                else (f"ddflow release -- {item}")
            )
        return f"ddflow update {item} --globs {globs}" if self.keep else f"ddflow release {item}"

    def mcp(self) -> str:
        if self.keep:
            return f"ddflow_task action=update id={self.item} globs={','.join(self.keep)}"
        return f"ddflow_release id={self.item}"

    def as_dict(self) -> dict[str, Any]:
        return {"item": self.item, "release": list(self.release), "keep": list(self.keep)}


@dataclass(frozen=True)
class Link:
    """One edge of a cycle: ``agent`` (on ``item``, its oldest wait there) waits for
    ``holder`` (on ``blocker``) over the ``files`` of the holder's leases its requests
    overlap."""

    agent: str
    item: str
    holder: str
    blocker: str
    files: tuple[str, ...]
    since: float
    #: every lease of ``holder`` in the way of these waits (``blocker`` is the first)
    blockers: tuple[str, ...] = ()
    #: every item by which ``agent`` waits for ``holder`` (``item`` is the first)
    items: tuple[str, ...] = ()
    #: the globs those waits asked for
    asked: tuple[str, ...] = ()


@dataclass(frozen=True)
class Cycle:
    """A closed loop of waits. ``agents`` and ``items`` are in cycle order, from the
    smallest agent; ``victim`` is the agent asked to yield, ``drops`` what each of its leases
    gives up and ``release`` all those globs (empty when it holds none of the contested
    ones). ``victim_item`` and ``keep`` describe the first of ``drops``; read ``drops`` when
    there may be several."""

    agents: tuple[str, ...]
    items: tuple[str, ...]
    files: tuple[str, ...]
    links: tuple[Link, ...]
    victim: str
    victim_item: str
    release: tuple[str, ...]
    keep: tuple[str, ...] = field(default=())
    remedy: str = ""
    drops: tuple[Drop, ...] = ()

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
            "drops": [d.as_dict() for d in self.drops],
            "links": [
                {
                    "agent": k.agent,
                    "item": k.item,
                    "holder": k.holder,
                    "blocker": k.blocker,
                    "blockers": list(k.blockers),
                    "items": list(k.items),
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


@dataclass(frozen=True)
class Alarm:
    """``agent`` has waited ``age_s`` on ``item`` and no lease in its way shows work:
    ``on`` is ``(item, holder, idle_s)`` per lease, ``drops`` the smallest release."""

    item: str
    agent: str
    age_s: float
    on: tuple[tuple[str, str, float], ...]
    drops: tuple[Drop, ...]

    @property
    def key(self) -> str:
        """The one dedupe key of this alarm (r-alert-owner-next-call): a wait is per item."""
        return f"wait_alarm:{self.item}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "item": self.item,
            "agent": self.agent,
            "age_s": round(self.age_s, 1),
            "owner": self.on[0][1],
            "on": [{"item": i, "holder": h, "idle_s": round(s, 1)} for i, h, s in self.on],
            "drops": [d.as_dict() for d in self.drops],
            "commands": [d.command() for d in self.drops],
            "mcp": [d.mcp() for d in self.drops],
        }

    def render(self) -> str:
        way = ", ".join(f"{i} ({h}, idle {clock.fmt_age(s)})" for i, h, s in self.on)
        cmds = "; ".join(f"`{d.command()}`" for d in self.drops)
        fix = f"{cmds}, then {self.agent} retries" if cmds else "ask the holder to finish"
        return (
            f"lease wait alarm: {self.agent} on {self.item} has waited "
            f"{clock.fmt_age(self.age_s)} and nothing runs on {way}. {fix}."
        )


def _contested(lease: Held | None, asked: tuple[str, ...]) -> tuple[str, ...]:
    if lease is None:
        return ()
    return tuple(sorted({g for g in lease.globs if any(overlap(g, a) for a in asked)}))


def _drop(lease: Held, asked: tuple[str, ...]) -> Drop | None:
    """What ``lease`` gives up to a wait for ``asked``; None when it holds none of it."""
    wanted = _contested(lease, asked)
    if not wanted:
        return None
    return Drop(lease.item, wanted, tuple(g for g in lease.globs if g not in wanted))


def _wait_command(item: str) -> str:
    return f"ddflow wait --item{'=' if item.startswith('-') else ' '}{quote(item)}"


def _remedy(victim: Link, prev: Link, drops: tuple[Drop, ...]) -> str:
    """The sentence for ``victim``: ``drops`` are its leases ``prev`` (the waiter for the
    victim's lease) is blocked on."""
    waits = ", ".join(f"`{_wait_command(b)}`" for b in victim.blockers)
    then = (
        f"then {prev.agent} retries on {', '.join(prev.items)}, and {victim.agent} waits for "
        f"{victim.holder}'s lease ({waits})"
    )
    if not drops:
        return f"{victim.agent} should stop waiting and report to the coordinator; {then}."
    wanted = ", ".join(g for d in drops for g in d.release)
    cmds = "; ".join(f"`{d.command()}`" for d in drops)
    return f"{victim.agent} yields {wanted}: {cmds}; {then}."


def _joined(old: Link, w: Wait, blocker: str, files: tuple[str, ...]) -> Link:
    """``old`` with one more lease in the way, by the same wait or another of its agent."""
    return replace(
        old,
        blockers=tuple(dict.fromkeys((*old.blockers, blocker))),
        files=tuple(sorted({*old.files, *files})),
        items=tuple(dict.fromkeys((*old.items, w.item))),
        asked=tuple(dict.fromkeys((*old.asked, *w.globs))),
    )


def _edges(waits: list[Wait], held: dict[str, Held]) -> dict[str, dict[str, Link]]:
    """agent -> blocking agent -> ONE link for all its waits there, led by the oldest: a
    younger wait on another lease of that holder stays blocked unless that lease drops too."""
    out: dict[str, dict[str, Link]] = {}
    for w in sorted(waits, key=lambda w: (w.since, w.item)):
        for blocker, holder in w.on:
            lz = held.get(blocker)
            if lz is None or lz.holder != holder:
                continue  # let go (or taken by another) since the record was made
            files = _contested(lz, w.globs)
            row = out.setdefault(w.agent, {})
            old = row.get(holder)
            row[holder] = (
                _joined(old, w, blocker, files)
                if old
                else Link(
                    w.agent, w.item, holder, blocker, files, w.since, (blocker,), (w.item,), w.globs
                )
            )
    return out


def _cycle(links: list[Link], leases: list[Held]) -> Cycle:
    """The report for one ring of ``links``: victim, what it releases, the remedy."""
    by_item = {lz.item: lz for lz in leases}

    def worked_at(i: int) -> float:
        """The newest work on the leases of ``links[i].agent`` that the ring waits for."""
        return max(by_item[b].progress_at for b in links[i - 1].blockers)

    # Least progress first; a tie (or nothing measured) is the youngest wait, the later agent.
    vi = max(range(len(links)), key=lambda i: (-worked_at(i), links[i].since, links[i].agent))
    victim = links[vi]
    prev = links[vi - 1]  # whoever waits for the victim's lease, and the lease it waits on
    owns = [lz for lz in leases if lz.item in prev.blockers]
    drops = tuple(d for lz in owns if (d := _drop(lz, prev.asked)))
    return Cycle(
        agents=tuple(lk.agent for lk in links),
        items=tuple(dict.fromkeys(i for lk in links for i in (*lk.items, *lk.blockers))),
        files=tuple(sorted({f for lk in links for f in lk.files})),
        links=tuple(links),
        victim=victim.agent,
        victim_item=drops[0].item if drops else victim.item,
        release=tuple(dict.fromkeys(g for d in drops for g in d.release)),
        keep=drops[0].keep if drops else (),
        remedy=_remedy(victim, prev, drops),
        drops=drops,
    )


def find(waits: list[Wait], leases: list[Held]) -> list[Cycle]:
    """Every wait-for cycle among ``waits`` given the live ``leases``, ordered by their
    smallest agent. A chain that does not close is not a cycle; a wait whose blockers
    have let go (its ``on`` names no live lease) contributes no edge."""
    out = _edges(waits, {lz.item: lz for lz in leases})
    nodes = {a: a for a in out} | {h: h for row in out.values() for h in row}
    rings = find_cycles(nodes, lambda a: sorted(out.get(a, {}), reverse=True))
    return sorted(
        (_cycle([out[a][b] for a, b in pairwise(r)], leases) for r in rings),
        key=lambda c: c.agents,
    )


def _alarm(w: Wait, held: dict[str, Held], now: float, limit: float) -> Alarm | None:
    """``w``'s alarm, or None while a lease in its way shows work or none stands in it."""
    way = [held[b] for b, h in w.on if b in held and held[b].holder == h]
    on = tuple((lz.item, lz.holder, now - lz.progress_at) for lz in way)
    if not on or min(s for _i, _h, s in on) < limit:
        return None
    drops = tuple(d for lz in way if (d := _drop(lz, w.globs)))
    return Alarm(w.item, w.agent, now - w.since, on, drops)


def alarms(
    waits: list[Wait], leases: list[Held], cycles: list[Cycle], now: float, limit: float
) -> list[Alarm]:
    """Every wait older than ``limit`` whose leases in the way have ALL shown no work for
    ``limit``, oldest first. A wait in one of ``cycles`` is that cycle's report; a wait
    behind no live lease (a place in a queue) has nothing to release; 0 turns this off."""
    if limit <= 0:
        return []
    ringed = {(i, k.agent) for c in cycles for k in c.links for i in k.items}
    held = {lz.item: lz for lz in leases}
    aged = [w for w in waits if now - w.since >= limit and (w.item, w.agent) not in ringed]
    found = (_alarm(w, held, now, limit) for w in sorted(aged, key=lambda w: (w.since, w.item)))
    return [a for a in found if a]
