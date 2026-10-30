"""The swarm scheduler core: hard constraints, then a reasoned score. Pure.

No I/O, no clock, no randomness, no model call: everything arrives as arguments (the
caller passes the ledger, the lease snapshot and, for exploration, its own uniform draw),
so the same inputs give the same answer and a dispatch can be replayed from its note.

Two stages (D-swarm-answers Q1, Q5, Q10):

1. ``refusals``: hard constraints, each a reason string. Open needs, glob overlap with
   another item's live lease, the global, per-family and per-lane caps, hedging cap, the
   window, the trust tier, and family independence (a reviewer's family differs from the
   author's; an author needs a reviewer family left that differs from its own). A refused
   pair is never scored into eligibility: no weight can override a refusal.
2. ``evaluate``: a score from critical path, fit (ledger), cost, rework and queue, each a
   term in [0, 1] times its ``[swarm]`` weight, kept in ``Decision.parts``. Cost only ranks
   lanes whose fit is within ``fit_margin`` of the best eligible lane (``rank``), so a free
   local route never wins by being free. An unmeasured pair scores neutral, and an
   ``unavailable``/``partial`` outcome is never a pass (``review_satisfied`` and the ledger).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace

from ...core.globs import overlap
from . import ledger as L
from .score_config import Weights

NEUTRAL = 0.5  # the term for a figure nothing has measured: no bonus, no penalty
MTOK = 1_000_000.0


@dataclass(frozen=True)
class Item:
    id: str
    kind: str = "task"
    globs: tuple[str, ...] = ()
    needs: tuple[str, ...] = ()
    critical: float = 0.0  # 0..1: share of the longest dependency chain this item sits on
    waited_s: float = 0.0
    min_trust: int | None = None  # None: [swarm] default_min_trust
    est_in: int = 0  # estimated input / output tokens
    est_out: int = 0
    attempts: int = 0  # live attempts already on it, the first included; hedge_max extra allowed


@dataclass(frozen=True)
class Lane:
    name: str
    family: str
    model: str
    trust: int = 0
    price_in: float | None = None  # USD per million tokens; None: not verified
    price_out: float | None = None
    window: int | None = None
    local: bool = False  # a local route costs zero
    live: int = 0
    cap: int = 1
    kinds: tuple[str, ...] = ()  # empty: any kind


@dataclass(frozen=True)
class Live:
    item: str
    globs: tuple[str, ...]
    family: str = ""


@dataclass(frozen=True)
class World:
    done: frozenset[str] = frozenset()  # items whose work is merged
    live: tuple[Live, ...] = ()
    ledger: L.Ledger = field(default_factory=lambda: L.Ledger({}))
    reviewer_families: tuple[str, ...] | None = None  # None: independence not checkable


@dataclass(frozen=True)
class Decision:
    item: str
    lane: str
    ok: bool
    score: float
    parts: tuple[tuple[str, float], ...]
    reasons: tuple[str, ...]
    family: str = ""
    model: str = ""
    fit: float = NEUTRAL


def review_satisfied(author_family: str, reviews: Iterable[tuple[str, str]]) -> bool:
    """Is there a review of outcome ``passed`` by a family other than the author's?
    ``reviews`` are (family, outcome); unavailable, partial, skipped and failed never count."""
    return any(o == "passed" and f and f != author_family for f, o in reviews)


def est_cost(item: Item, lane: Lane) -> float | None:
    """Estimated USD, 0 for a local route, None when a price is unverified."""
    if lane.local:
        return 0.0
    if (item.est_in and lane.price_in is None) or (item.est_out and lane.price_out is None):
        return None
    return (item.est_in * (lane.price_in or 0.0) + item.est_out * (lane.price_out or 0.0)) / MTOK


def _state_refusals(item: Item, world: World, w: Weights) -> list[str]:
    out: list[str] = []
    open_needs = sorted(n for n in item.needs if n not in world.done)
    if open_needs:
        out.append("open needs: " + ", ".join(open_needs))
    for lv in world.live:
        if lv.item != item.id and any(overlap(a, b) for a in item.globs for b in lv.globs):
            out.append(f"globs overlap the live lease on {lv.item}")
    if len(world.live) >= w.max_lanes:  # every live lease, hedges included
        out.append(f"swarm at max_lanes ({w.max_lanes})")
    if item.attempts > w.hedge_max:
        out.append(f"hedge cap: {item.attempts} attempts, hedge_max {w.hedge_max}")
    return out


def _lane_refusals(item: Item, lane: Lane, world: World, w: Weights) -> list[str]:
    out: list[str] = []
    if lane.live >= lane.cap:
        out.append(f"lane {lane.name} at its cap ({lane.cap})")
    if sum(lv.family == lane.family for lv in world.live) >= w.family_cap:
        out.append(f"family {lane.family or '(unknown)'} at family_cap ({w.family_cap})")
    if lane.kinds and item.kind not in lane.kinds:
        out.append(f"lane does not take kind {item.kind}")
    need = w.default_min_trust if item.min_trust is None else item.min_trust
    if lane.trust < need:
        out.append(f"trust tier {lane.trust} below the required {need}")
    if lane.window is not None and item.est_in + item.est_out > lane.window:
        out.append(f"estimate {item.est_in + item.est_out} tokens exceeds window {lane.window}")
    return out


def _independence(lane: Lane, world: World, role: str, author_family: str) -> list[str]:
    if role == "reviewer":
        if not lane.family or not author_family or lane.family == author_family:
            return ["reviewer family must differ from the author's (independence)"]
    elif world.reviewer_families is not None and not any(
        f and f != lane.family for f in world.reviewer_families
    ):
        return ["no reviewer family differs from the author's (independence)"]
    return []


def refusals(
    item: Item,
    lane: Lane,
    world: World,
    w: Weights,
    *,
    role: str = "author",
    author_family: str = "",
) -> list[str]:
    """Every hard constraint ``lane`` fails for ``item``; empty means eligible."""
    return [
        *_state_refusals(item, world, w),
        *_lane_refusals(item, lane, world, w),
        *_independence(lane, world, role, author_family),
    ]


def _terms(item: Item, lane: Lane, world: World, w: Weights) -> tuple[dict[str, float], list[str]]:
    cell = world.ledger.cell(lane.family, lane.model, item.kind)
    why: list[str] = []
    cost = est_cost(item, lane)
    if cost is None:
        cost_t = NEUTRAL
        why.append("price unverified: neutral cost term")
    else:
        cost_t = 1.0 - (min(1.0, cost / w.cost_ref_usd) if w.cost_ref_usd > 0 else float(cost > 0))
    if cell.items:
        rate = (cell.rework + cell.bugs) / cell.items
        rework_t = 1.0 - (min(1.0, rate / w.rework_ref) if w.rework_ref > 0 else float(rate > 0))
    else:
        rework_t = NEUTRAL
        why.append(f"unmeasured pair on {item.kind}: neutral fit prior")
    terms = {
        "critical": min(1.0, max(0.0, item.critical)),
        "fit": cell.score(),
        "cost": cost_t,
        "rework": rework_t,
        "queue": min(1.0, max(0.0, item.waited_s) / w.queue_ref_s) if w.queue_ref_s else 0.0,
    }
    return terms, why


def evaluate(
    item: Item,
    lane: Lane,
    world: World,
    w: Weights,
    *,
    role: str = "author",
    author_family: str = "",
) -> Decision:
    """One (item, lane) pair: refused with reasons, or scored with its parts."""
    refused = refusals(item, lane, world, w, role=role, author_family=author_family)
    terms, why = _terms(item, lane, world, w)
    weights = {
        "critical": w.w_critical,
        "fit": w.w_fit,
        "cost": w.w_cost,
        "rework": w.w_rework,
        "queue": w.w_queue,
    }
    parts = tuple((k, weights[k] * terms[k]) for k in weights)
    return Decision(
        item=item.id,
        lane=lane.name,
        ok=not refused,
        score=sum(v for _, v in parts),
        parts=parts,
        reasons=tuple(refused) if refused else tuple(why),
        family=lane.family,
        model=lane.model,
        fit=terms["fit"],
    )


def rank(
    item: Item,
    lanes: Sequence[Lane],
    world: World,
    w: Weights,
    *,
    role: str = "author",
    author_family: str = "",
) -> list[Decision]:
    """Eligible lanes best first, then refused ones. Lanes whose fit trails the best
    eligible lane by more than ``fit_margin`` rank after those inside it, whatever their
    score, so cost never buys a place over better qualification."""
    ds = [evaluate(item, ln, world, w, role=role, author_family=author_family) for ln in lanes]
    good = [d for d in ds if d.ok]
    best = max((d.fit for d in good), default=0.0)
    floor = best - w.fit_margin - 1e-12

    def key(d: Decision) -> tuple:
        return (d.fit < floor, -d.score, d.lane)

    out = [
        replace(d, reasons=(*d.reasons, "outqualified: fit below margin")) if d.fit < floor else d
        for d in sorted(good, key=key)
    ]
    return out + sorted((d for d in ds if not d.ok), key=lambda d: d.lane)


def choose(
    item: Item,
    lanes: Sequence[Lane],
    world: World,
    w: Weights,
    *,
    draw: float | None = None,
    role: str = "author",
    author_family: str = "",
) -> Decision | None:
    """The lane for ``item``, or None when no lane is eligible. With a ``draw`` under
    ``explore_share``, the least-sampled eligible pair under ``explore_min_n`` instead."""
    ranked = rank(item, lanes, world, w, role=role, author_family=author_family)
    good = [d for d in ranked if d.ok]
    if not good:
        return None
    if draw is not None:
        pick = L.explore(
            world.ledger,
            sorted({(d.family, d.model) for d in good}),
            item.kind,
            draw=draw,
            share=w.explore_share,
            min_n=w.explore_min_n,
        )
        for d in good:
            if pick == (d.family, d.model):
                return replace(d, reasons=(*d.reasons, "explore: under-measured"))
    return good[0]


def pull(
    lane: Lane,
    items: Sequence[Item],
    world: World,
    w: Weights,
    *,
    role: str = "author",
    author_family: str = "",
) -> Decision | None:
    """What an idle ``lane`` should take: the best-scoring eligible item (ties by id).
    A reviewer lane passes ``role="reviewer"`` and the author's family."""
    ds = [
        evaluate(it, lane, world, w, role=role, author_family=author_family)
        for it in sorted(items, key=lambda i: i.id)
    ]
    good = [d for d in ds if d.ok]
    return max(good, key=lambda d: (d.score, -good.index(d))) if good else None
