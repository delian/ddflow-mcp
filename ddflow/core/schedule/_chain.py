"""Critical-path chains: `chain_cycles` and `critical_path`."""

from __future__ import annotations

from collections.abc import Callable

from ...config import Config
from ..graph import closure, find_cycles, longest_chains
from ..model import DONE, Item, State
from ._readiness import inherited_deps


def _chain_scope(
    state: State, phase: str, cfg: Config | None = None
) -> tuple[dict[str, Item], Callable[[Item], list[str]]]:
    """The live items a critical path walks (a phase's whole closure, or all) and the edge
    function it uses: what must finish before an item can."""

    def before(it: Item) -> list[str]:
        """What must finish before ``it`` can: its dependencies, inherited ones too, and
        -- an umbrella, task or phase, closing only when what is under it does -- its
        children (B79c2f6e17a: without them a split task's chain, or the chain inside a
        phase another depends on, read one step short per level)."""
        deps = [d for _owner, d in inherited_deps(state, it, cfg)]
        return deps + [c.id for c in state.children(it.id) if not c.removed]

    live = state.live_by_id()
    if phase:
        # The phase, EVERY item below it -- a chain of sub-tasks nested under a task is
        # the phase's work too (B13ed484062) -- and everything those wait on, wherever
        # it lives: a phase that needs another cannot start before that one's chain is
        # done, so that chain is part of this phase's floor.
        inside: set[str] = set()
        if phase in live:
            inside = {phase, *closure(phase, lambda n: [d for d in before(live[n]) if d in live])}
        live = {k: v for k, v in live.items() if k in inside}
    return live, before


def chain_cycles(
    state: State,
    phase: str = "",
    scope: tuple[dict[str, Item], Callable[[Item], list[str]]] | None = None,
    cfg: Config | None = None,
) -> list[list[str]]:
    """Cycles among the NOT-DONE items of a critical path's scope, by the edges it walks.

    Finished work cannot be waited on, so a loop that runs through it is no cycle
    (Bbbcbfc4bbb: a whole-project forecast refused over one, while `next` found none)."""
    live, before = scope or _chain_scope(state, phase, cfg)
    open_items = {k: v for k, v in live.items() if v.state != DONE}
    return find_cycles(open_items, edges=lambda it: [d for d in before(it) if d in open_items])


def critical_path(state: State, phase: str = "", cfg: Config | None = None) -> list[str]:
    """Longest dependency chain among not-done items — the true wall-clock floor.

    Reporting it is what stops an operator adding a fifth parallel agent to a phase
    whose runtime is set by a four-deep chain: total time equals the longest path, not
    the sum of the work.
    """
    items, before = _chain_scope(state, phase, cfg)

    # The longest chain is only defined on an acyclic graph: on a cyclic one a chain
    # computed for one node is reused where it is wrong. Cycles are reachable via `cycle_policy = "warn"`, so refuse rather
    # than return a confidently wrong number.
    if chain_cycles(state, phase, (items, before), cfg):
        return []
    # INHERITED dependencies, like readiness uses. The path used to walk `it.needs`
    # alone, so a phase-level dependency did not lengthen the reported floor at all --
    # and the number exists precisely to stop someone adding a fifth agent to a phase
    # whose runtime is set by a chain.
    open_ids = [i for i, it in items.items() if it.state != DONE]
    longest = longest_chains(open_ids, lambda n: before(items[n]))

    def trimmed(chain: list[str]) -> list[str]:
        """A phase at the END only closes the chain it holds -- no work of its own, and
        nothing after it waits on it here. Inside a chain it stays: it is the boundary a
        phase dependency waits on. Trimmed BEFORE the chains are compared, or a chain
        long only by its trailing phases beat a longer chain of real work."""
        while chain and not items[chain[-1]].is_work:
            chain = chain[:-1]
        return chain

    chains = [trimmed(longest[i]) for i in open_ids]
    return max(chains, key=len) if chains else []
