"""Dependency waves (D-wave-semantics): the one derivation behind `ddflow waves`, `--stamp`,
`--check` and the default restamp.

wave 0 = no OPEN prerequisite, task-level or inherited from a phase or umbrella; wave N = 1 +
the highest wave among open prerequisites. A phase as a prerequisite takes the wave of its
last open task (it adds no level of its own); a task that is an umbrella waits on its open
children. Glob overlap is ignored (that split is the advisory `core.schedule.waves` number).
An item on a cycle, or waiting on one, has NO wave. Priority orders items inside a wave and
never moves one across waves. Pure: it reads a `State` and returns data.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from graphlib import TopologicalSorter

from ..core.graph import find_cycles
from ..core.schedule import inherited_deps

WAVE_TAG = re.compile(r"wave:\d+")


def open_items(state) -> dict:
    """Live items that are neither finished nor removed."""
    return {i: it for i, it in state.live_by_id().items() if not it.terminal}


def prerequisites(state, cfg, live: dict) -> dict[str, list[str]]:
    """For each open item the open items that must finish before it can."""
    out: dict[str, list[str]] = {}
    for iid, it in live.items():
        deps = [d for _owner, d in inherited_deps(state, it, cfg)]
        deps += [c.id for c in state.children(iid) if not c.removed]
        out[iid] = [d for d in dict.fromkeys(deps) if d in live]
    return out


def derive(state, cfg) -> dict[str, int | None]:
    """The wave of every open work item (a phase is not stamped): an int, or None on a cycle."""
    live = open_items(state)
    pre = prerequisites(state, cfg, live)
    bad = {i for cycle in find_cycles(pre, list) for i in cycle}
    value: dict[str, int | None] = {}
    for iid in _order(pre, bad):
        if iid in bad or any(value.get(d, -2) is None for d in pre[iid]):
            value[iid] = None
            continue
        top = max((value[d] for d in pre[iid]), default=-1)
        value[iid] = top if live[iid].kind == "phase" else top + 1
    return {i: v for i, v in value.items() if live[i].kind != "phase"}


def _order(pre: dict[str, list[str]], bad: set[str]) -> list[str]:
    """A dependency-first order of the items off the cycles (cycle members come first, as
    the None they are), so every other item is placed after what it waits on."""
    ok = {i: [d for d in ds if d not in bad] for i, ds in pre.items() if i not in bad}
    return [*sorted(bad), *TopologicalSorter(ok).static_order()]


def stamped(tags) -> list[str]:
    return [t for t in tags if WAVE_TAG.fullmatch(t)]


@dataclass
class Plan:
    waves: dict[str, int | None]
    edits: list[dict] = field(default_factory=list)  # ItemEdit entries for `batch`
    cyclic: list[str] = field(default_factory=list)


def plan(state, cfg) -> Plan:
    """The waves and the minimal tag edits that make every open item's stamp match."""
    waves = derive(state, cfg)
    edits = []
    for iid, w in sorted(waves.items()):
        have = stamped(state.items[iid].tags)
        want = [] if w is None else [f"wave:{w}"]
        if have == want:
            continue
        entry: dict = {"id": iid}
        if want and want[0] not in have:
            entry["add_tags"] = want
        if stale := [t for t in have if t not in want]:
            entry["remove_tags"] = stale
        edits.append(entry)
    # A live phase is never stamped, so a hand-edited `wave:*` on one is stale (D-labels:
    # the namespace is derived and reserved): remove it. A terminal item is left alone --
    # a batch refuses to edit it, and its `wave:*` is history, not a stamp to keep current
    # (Bf03dc5a256 review).
    for iid, it in open_items(state).items():
        if it.kind != "phase":
            continue
        if stale := stamped(it.tags):
            edits.append({"id": iid, "remove_tags": stale})
    return Plan(waves, edits, sorted(i for i, w in waves.items() if w is None))
