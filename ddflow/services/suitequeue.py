"""Who goes next in the full-suite queue: classes, cost-aware order, aging, fair share, an ETA.

Pure ordering over what the tickets say, plus a small per-host store of measured run seconds that
predicts a ticket's cost (SQ-priority-fair-queue; R-suite-lock-principles-2026-11-12).

Order, with ``waiting`` = the live tickets:

1. a ticket that has waited ``[suites].max_wait_factor`` times its own predicted cost is OVERDUE
   and goes first (oldest first): nothing waits past that bound whatever arrives behind it;
2. then by class: a fix of red main and the merge-queue integrator first, then a phase's closing
   run, then an item's run, then a baseline measurement;
3. within a class the shortest predicted run first (a short run ahead of a long one cuts the mean
   wait); ties by arrival;
4. then the fair share: one agent leads at most ``[suites].agent_share`` of the waiting tickets,
   its others queue behind the rest (never dropped: a lone agent keeps its own order).

A cost of 0 means unknown and counts as ``[suites].default_cost_s``."""

from __future__ import annotations

import contextlib
import json
import math
import re
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..infra import fsio

#: The classes, highest first. A ticket of any other class ranks as an item.
CLASSES = ("red_main", "integrator", "phase", "item", "baseline")
ITEM = "item"
COSTS = "costs.json"
SECONDS_PER_MINUTE = 60


@dataclass(frozen=True)
class Waiting:
    """One live ticket as the order sees it."""

    name: str
    klass: str
    cost_s: float
    agent: str
    waited_s: float


def classify(holder: str, patterns: dict[str, list[str]]) -> str:
    """The class of a ticket from its holder label: the first class (in rank order) one of whose
    patterns the label matches; an item when none does."""
    for klass in CLASSES:
        if any(re.search(p, holder) for p in patterns.get(klass, ())):
            return klass
    return ITEM


def _rank(klass: str) -> int:
    return CLASSES.index(klass) if klass in CLASSES else CLASSES.index(ITEM)


def order(
    waiting: list[Waiting], *, max_wait_factor: float, agent_share: float, default_cost_s: float
) -> list[Waiting]:
    """``waiting`` in the order the slots are offered (see the module docstring)."""

    def cost(w: Waiting) -> float:
        return w.cost_s if w.cost_s > 0 else default_cost_s

    def overdue(w: Waiting) -> bool:
        return w.waited_s >= max_wait_factor * max(cost(w), 1.0)

    def key(w: Waiting) -> tuple:
        if overdue(w):  # first, oldest first
            return (0, 0, 0.0, -w.waited_s)
        return (1, _rank(w.klass), cost(w), -w.waited_s)

    ranked = sorted(waiting, key=key)
    if len({w.agent for w in ranked}) <= 1:
        return ranked  # nobody to be fair to
    cap = max(1, math.ceil(agent_share * len(ranked)))
    seen: dict[str, int] = {}
    kept: list[Waiting] = []
    deferred: list[Waiting] = []
    for w in ranked:
        seen[w.agent] = seen.get(w.agent, 0) + 1
        # The aging bound outranks the share: an overdue run is never deferred, but it counts
        # against its agent's cap, so the agent's other runs wait behind everybody else's.
        (kept if overdue(w) or seen[w.agent] <= cap else deferred).append(w)
    return kept + deferred


def estimate(
    ordered: list[Waiting], name: str, *, free_in_s: list[float], default_cost_s: float = 600.0
) -> dict:
    """Where ``name`` stands: its place, the tickets ahead, the seconds it should wait and its own
    predicted run. ``free_in_s`` is when each slot frees (0 for an idle one, the running suite's
    remaining seconds for a held one); the tickets ahead take the earliest-free slot in turn, and
    ``name`` waits for the earliest that is left. An unknown cost counts as ``default_cost_s``."""
    names = [w.name for w in ordered]
    at = names.index(name) if name in names else len(names)

    def cost(w: Waiting) -> float:
        return w.cost_s if w.cost_s > 0 else default_cost_s

    free = sorted(max(0.0, t) for t in free_in_s) or [0.0]
    for w in ordered[:at]:
        free.sort()
        free[0] += cost(w)
    mine = cost(ordered[at]) if at < len(ordered) else 0.0
    return {"position": at + 1, "ahead": at, "wait_s": min(free), "predicted_s": mine}


def _minutes(seconds: float) -> str:
    if seconds < SECONDS_PER_MINUTE:
        return f"{seconds:.0f}s"
    return f"{seconds / SECONDS_PER_MINUTE:.0f}m"


def say_estimate(est: dict) -> str:
    """What follows ``you are #4 in the queue``: ``estimated wait 14m (3 ahead, predicted 31m)``."""
    return (
        f"estimated wait {_minutes(est['wait_s'])} "
        f"({est['ahead']} ahead, predicted {_minutes(est['predicted_s'])})"
    )


# -- the cost store -------------------------------------------------------------------------


def _read(directory: Path) -> dict[str, list[float]]:
    try:
        data = json.loads((directory / COSTS).read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def record_cost(directory: Path, key: str, seconds: float, *, keep: int) -> None:
    """Remember that a run of ``key`` (its gate and scope) took ``seconds``: the newest ``keep``."""
    directory.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError, fsio.LockTimeout):
        with fsio.file_lock(directory / "costs.lock", 5.0):
            data = _read(directory)
            runs = [float(x) for x in data.get(key, []) if isinstance(x, int | float)]
            data[key] = [*runs, float(seconds)][-max(1, keep) :]
            fsio.replace_text(directory / COSTS, json.dumps(data), fsync=False)


def predicted_cost(directory: Path, key: str, *, keep: int) -> float:
    """The median of the newest ``keep`` measured runs of ``key``; 0.0 (unknown) with none."""
    runs = [x for x in _read(directory).get(key, []) if isinstance(x, int | float)]
    return float(statistics.median(runs[-max(1, keep) :])) if runs else 0.0


# -- the ticket's note ----------------------------------------------------------------------


def write_note(path: Path, klass: str, cost_s: float, agent: str) -> None:
    with contextlib.suppress(OSError):
        fsio.write_in_place(path, json.dumps({"class": klass, "cost_s": cost_s, "agent": agent}))


def read_note(path: Path) -> dict[str, Any]:
    try:
        note = json.loads(path.read_text() or "{}")
    except (OSError, ValueError):
        return {}
    return note if isinstance(note, dict) else {}
