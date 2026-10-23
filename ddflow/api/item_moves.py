"""`update --parent` / `--kind`: moving a task to another phase, and changing its kind.

Planned, not recorded: `items.plan_edit` folds the result into the edit's fields. A string
is the reason the move is refused.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from ..core.flow import effective_line
from ..core.graph import find_cycles
from ..core.records import WORK_KINDS, Item, State
from ..core.schedule import inherited_deps


def plan(
    cfg,
    st: State,
    it: Item,
    parent: str | None,
    kind: str | None,
    line_frozen: Callable[[State, Item], str],
    needs: list[str] | None = None,
) -> dict[str, Any] | str:
    """``{"parent": ..}`` / ``{"kind": ..}`` for the parts that change ``it``, else why not.

    Both are for live work that is not finished: a done item is history, and a phase is a
    container (always top level, always a phase).
    """
    out: dict[str, Any] = {}
    if parent is None and kind is None:
        return out
    if it.terminal:
        return f"{it.id} is {it.state}: a finished item is not moved or retyped"
    if not it.is_work:
        what = "moved" if parent is not None else "given a work kind"
        return f"{it.id} is a phase: it is top level, and cannot be {what}"
    if kind is not None and kind not in WORK_KINDS:
        return f"unknown kind {kind!r}: use {', '.join(WORK_KINDS)}"
    if kind is not None and kind != it.kind:
        out["kind"] = kind
    if parent is not None and parent != it.parent:
        bad = _parent_problem(cfg, st, it, parent, line_frozen, needs)
        if bad:
            return bad
        out["parent"] = parent
    return out


def _parent_problem(
    cfg,
    st: State,
    it: Item,
    parent: str,
    line_frozen: Callable[[State, Item], str],
    needs: list[str] | None,
) -> str:
    to = st.items.get(parent) if parent else None
    if parent:
        if to is None or to.removed:
            return f"no such parent {parent!r}. Add the phase or task first."
        if parent == it.id or parent in st.descendants(it.id):
            return (
                f"{parent} is {it.id} itself or beneath it: that would make {it.id} its own "
                f"ancestor"
            )
        if to.terminal:
            return f"{parent} is {to.state}: nothing is moved under a finished item"
        if to.is_work and to.lease is not None:
            return (
                f"{parent} is claimed ({to.lease.holder}): a task moved under it makes it an "
                f"umbrella, which holds no lease. Release it first."
            )
    trial = _moved(st, it, parent, needs)
    if effective_line(trial, trial.items[it.id]) != effective_line(st, it):
        # An item inherits its phase's release line: moving it re-aims it like `--line` does.
        frozen = line_frozen(st, it)
        if frozen:
            return f"{parent or 'top level'} is on another release line than {it.parent or 'its phase'}. {frozen}"
    cycle = _new_cycle(cfg, st, trial)
    if cycle:
        return (
            f"moving {it.id} under {parent} would close a dependency cycle ("
            + " -> ".join(cycle)
            + "): it would inherit what the new parent waits on"
        )
    return ""


def _moved(st: State, it: Item, parent: str, needs: list[str] | None) -> State:
    """A copy of ``st`` with ``it`` under ``parent`` (and with ``needs``, when the same edit
    sets them)."""
    trial = copy.copy(st)
    trial.__dict__.pop("_children_cache", None)  # the parent -> children index is per fold
    trial.items = {
        **st.items,
        it.id: replace(it, parent=parent, needs=it.needs if needs is None else list(needs)),
    }
    return trial


def _new_cycle(cfg, st: State, trial: State) -> list[str]:
    """A dependency cycle, through inherited needs, that exists with ``it`` under ``parent``
    and did not before -- or []. (The direct-needs graph `find_cycles` defaults to misses
    the one a phase's own needs make.)"""

    def cycles(state: State) -> set[tuple[str, ...]]:
        live = state.live_by_id()
        edges = lambda i: [d for _owner, d in inherited_deps(state, i, cfg)]  # noqa: E731
        return {tuple(c) for c in find_cycles(live, edges)}

    found = sorted(cycles(trial) - cycles(st))
    return list(found[0]) if found else []
