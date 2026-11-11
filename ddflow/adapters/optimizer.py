"""The `Optimizer` port: how ready work is ordered and cut to the free slots (ADP-optimizer).

Home of the `optimizer` family. The port is `Optimizer` (`OptimizerPort` is an alias), declared
below next to its adapter; the fake is `ddflow.adapters.fakes.optimizer.FakeOptimizer`. A family
with several adapters keeps them in `OPTIMIZERS`, keyed by `name`; `[schedule].optimizer` picks one
and `get_optimizer` resolves it.

* ``order(ctx, candidates)`` ranks the candidates: one `Placement` each, best first, every one
  carrying the itemised terms that put it there.
* ``cut(ctx, ready, slots, ...)`` walks an ordered ready list and decides, per item, whether it
  is taken, held, clashes with a taken item, or is cut for lack of a slot.
* ``explain(placement)`` says in words why an item sits where it does.

The default adapter, ``greedy-v1``, is the algorithm ddflow always had: the registered order
keys left to right (bugs first, priority, id) and a greedy cut that takes an item only when it
overlaps nothing already taken. ``swarm-v1`` is the second: the swarm scheduler's item order
(critical-path items first, then the longest-waiting) over the same cut. Affinity and aversion
are PREFERENCES that reorder, never filters: hard constraints stay `needs` and `globs`, decided
by the caller's readiness filters.

Deterministic given the ctx: the algorithm reads no clock, no hash order and no randomness,
and the id is the final tie-break; a fact such as ``waited_s`` is time-derived by the CALLER's
``now``, so the same ``ctx`` always ranks the same, but the fact itself ages as time moves. The
``ctx`` is an OPEN mapping: the caller fills ``ctx["facts"]`` with the per-candidate facts an
adapter may read (``critical``, ``waited_s``) and it reads only the keys it knows. An adapter
says which facts it reads with ``facts``; the default reads none, so the default plan pays for
none. Pure of ddflow: imports no other layer.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from .ports import port
from .registry import Registry

#: The adapter used when `[schedule].optimizer` names none.
DEFAULT_NAME = "greedy-v1"


@dataclass(frozen=True)
class Candidate:
    """One item as the optimizer sees it. ``terms`` are the caller's ordering keys, left to
    right, as ``(name, value)`` pairs (the registered keys of `core.schedule`); ``tags`` carry
    preferences such as ``affinity:<key>@50`` for an adapter that reads them; ``reserved`` says
    it may take a slot `Slots` keeps out of the ordinary share."""

    id: str
    terms: tuple[tuple[str, Any], ...] = ()
    globs: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    needs_tree: bool = True
    reserved: bool = False


@dataclass(frozen=True)
class Placement:
    """Where one candidate landed: ``rank`` (0 is first) and the itemised ``terms``."""

    id: str
    rank: int
    terms: tuple[tuple[str, Any], ...] = ()


@dataclass(frozen=True)
class Slots:
    """What is free: ``flight`` items in flight and ``tree`` worktrees (both counts), and of
    those the ``ordinary_*`` share an item not marked `Candidate.reserved` may take (None: all
    of it, nothing is reserved). A reserved item may take any free slot."""

    flight: int
    tree: int
    ordinary_flight: int | None = None
    ordinary_tree: int | None = None


@dataclass(frozen=True)
class Verdict:
    """What `cut` decided for one item, in walk order. ``what`` is ``taken``, ``held``,
    ``clash`` (``other`` names the taken item it overlaps), ``cut`` (no slot) or ``reserved``
    (a slot is free, but only for reserved work)."""

    id: str
    what: str
    other: str = ""


@dataclass(frozen=True)
class CutResult:
    """The verdicts of one cut, in the order the ready list was walked."""

    verdicts: tuple[Verdict, ...] = field(default_factory=tuple)

    def ids(self, what: str) -> list[str]:
        return [v.id for v in self.verdicts if v.what == what]


#: Whether an item is held back (a waiter in line): True drops it from the cut.
Hold = Callable[[Candidate], bool]
#: The taken item a candidate overlaps, or "" when it overlaps none.
Clash = Callable[[Candidate, Sequence[Candidate]], str]


def fit(c: Candidate, taken: Sequence[Candidate], slots: Slots) -> str:
    """The `Verdict.what` for ``c`` beside ``taken``: ``taken``, ``cut`` or ``reserved``."""
    if len(taken) >= slots.flight or (c.needs_tree and _trees(taken) >= slots.tree):
        return "cut"
    if c.reserved:
        return "taken"
    ordinary = [t for t in taken if not t.reserved]
    flight = slots.flight if slots.ordinary_flight is None else slots.ordinary_flight
    tree = slots.tree if slots.ordinary_tree is None else slots.ordinary_tree
    if len(ordinary) >= flight or (c.needs_tree and _trees(ordinary) >= tree):
        return "reserved"
    return "taken"


def _trees(items: Sequence[Candidate]) -> int:
    return sum(c.needs_tree for c in items)


def _term_text(value: Any) -> str:
    return str(int(value)) if isinstance(value, bool) else str(value)


def _fact(ctx: Mapping[str, Any], candidate_id: str) -> dict[str, Any]:
    """The per-candidate facts ``ctx["facts"][candidate_id]``, or ``{}`` when the ctx
    carries none. The ctx is OPEN: a caller fills the keys it knows and an adapter reads the
    ones it recognises, so an absent fact is a miss, never an error."""
    facts = ctx.get("facts", ())
    cell = facts.get(candidate_id, ()) if isinstance(facts, Mapping) else ()
    return cell if isinstance(cell, Mapping) else {}


def _fact_flag(cell: Mapping[str, Any], key: str) -> bool:
    """A flag fact, True only when truthy; an absent or ``None`` fact is neutral (False)."""
    value = cell.get(key)
    return bool(value)


def _fact_num(cell: Mapping[str, Any], key: str) -> float:
    """A numeric fact as a float, neutral ``0.0`` when absent, ``None``, not a number, or
    ``nan``/``inf``: the open ctx says "miss", never an error, and a quiet NaN that would
    break the deterministic total order reads as unmeasured."""
    value = cell.get(key)
    if value is None:
        return 0.0
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if math.isnan(number) or math.isinf(number):
        return 0.0
    return number


@port
@runtime_checkable
class Optimizer(Protocol):
    """Order ready work and cut it to the free slots.

    Home: `ddflow.adapters.optimizer`. Default adapter `GreedyV1`; the fake is `FakeOptimizer`.
    An adapter never filters: it reorders and cuts what the caller's readiness filters let
    through, and is deterministic (a total order, ties broken by id). An adapter also has a
    ``name`` (its registry key; not declared here: the port contract reserves that for `Forge`).
    """

    def order(self, ctx: Mapping[str, Any], candidates: Sequence[Candidate]) -> list[Placement]:
        """Every candidate once, best first, each with its itemised terms."""

    def cut(
        self,
        ctx: Mapping[str, Any],
        ready: Sequence[Candidate],
        slots: Slots,
        *,
        hold: Hold | None = None,
        clash: Clash | None = None,
    ) -> CutResult:
        """Walk ``ready`` in the order given; one `Verdict` per item. Never takes more than
        ``slots.flight`` items nor more tree-needing ones than ``slots.tree``, nor more
        ordinary ones than the ordinary share (`fit` decides all three)."""

    def explain(self, placement: Placement) -> str:
        """The placement's terms in words."""


OptimizerPort = Optimizer  # the name the task was filed under

OPTIMIZERS: Registry[Optimizer] = Registry("optimizer", key=lambda a: a.name, same_ok=True)  # type: ignore[attr-defined]


class GreedyV1:
    """The default adapter: ddflow's own ordering, unchanged. ``facts`` names the
    ``ctx["facts"]`` keys this adapter reads (none: its terms live on the candidate)."""

    name = DEFAULT_NAME
    facts: frozenset[str] = frozenset()

    def order(self, ctx: Mapping[str, Any], candidates: Sequence[Candidate]) -> list[Placement]:
        ranked = sorted(candidates, key=lambda c: (tuple(v for _, v in c.terms), c.id))
        return [Placement(c.id, n, c.terms) for n, c in enumerate(ranked)]

    def cut(
        self,
        ctx: Mapping[str, Any],
        ready: Sequence[Candidate],
        slots: Slots,
        *,
        hold: Hold | None = None,
        clash: Clash | None = None,
    ) -> CutResult:
        taken: list[Candidate] = []
        out: list[Verdict] = []
        for c in ready:
            if hold is not None and hold(c):
                out.append(Verdict(c.id, "held"))
                continue
            other = clash(c, taken) if clash is not None else ""
            if other:
                out.append(Verdict(c.id, "clash", other))
                continue
            what = fit(c, taken, slots)
            if what == "taken":
                taken.append(c)
            out.append(Verdict(c.id, what))
        return CutResult(tuple(out))

    def explain(self, placement: Placement) -> str:
        items = ", ".join(f"{k}={_term_text(v)}" for k, v in placement.terms)
        return f"{placement.id}: rank {placement.rank} by {items or 'id'}"


class SwarmV1(GreedyV1):
    """The swarm scheduler's own item order (critical-path items first, then the
    longest-waiting), over the SAME greedy cut: one overlap check, not a parallel design.

    Reads the per-candidate facts the caller fills into ``ctx["facts"]`` (``critical``: on the
    critical path, ``waited_s``: seconds since filed). A fact it does not find is neutral --
    the ctx is open and absent means unmeasured. Deterministic: the id breaks the last tie.
    """

    name = "swarm-v1"
    facts: frozenset[str] = frozenset({"critical", "waited_s"})

    def order(self, ctx: Mapping[str, Any], candidates: Sequence[Candidate]) -> list[Placement]:
        def key(c: Candidate) -> tuple:
            cell = _fact(ctx, c.id)
            return (
                not _fact_flag(cell, "critical"),
                -_fact_num(cell, "waited_s"),
                tuple(v for _, v in c.terms),
                c.id,
            )

        placed: list[Placement] = []
        for n, c in enumerate(sorted(candidates, key=key)):
            cell = _fact(ctx, c.id)
            terms = (
                ("critical", _fact_flag(cell, "critical")),
                ("waited_s", _fact_num(cell, "waited_s")),
            )
            placed.append(Placement(c.id, n, terms + tuple(c.terms)))
        return placed

    def explain(self, placement: Placement) -> str:
        items = ", ".join(f"{k}={_term_text(v)}" for k, v in placement.terms)
        return f"{placement.id}: rank {placement.rank} by {items}"


OPTIMIZERS.register(GreedyV1())
OPTIMIZERS.register(SwarmV1())


def get_optimizer(name: str = "") -> Optimizer:
    """The adapter named ``name`` (default `greedy-v1`); an unknown name lists those known."""
    return OPTIMIZERS.get_or_raise(name or DEFAULT_NAME)
