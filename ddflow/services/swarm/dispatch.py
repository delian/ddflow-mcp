"""Pull dispatch: the answer to an idle lane, over the scheduler core. Pure (SW-pull).

Takes what a caller already read and returns an ``Answer``: no I/O, no clock, no event.
``ddflow next`` is a read verb (r-read-verbs-read-only), so nothing here writes the dispatch
note: ``note`` is its text, the pull returns it, and the claim records it (``claim --note``).

``pull_for`` wires ``score.pull`` (a lane asks for an item) and ``place`` wires ``score.choose``
(an item asks for a lane, the call DX-dispatch-plan makes). They answer in this order:

1. ``unavailable``: a lane with no template, or a family, model or trust nobody can vouch for.
   Nothing is offered and nothing is defaulted (``gaps``). ``pull_for`` asks this itself;
   ``place`` takes lanes already built, so its caller keeps only sources ``gaps`` found complete.
2. ``hold``: the parallelism limit is reached or admission is paused (``Slots``).
3. ``none``: nothing is eligible, with the first refusals as the reason.
4. ``offer``: the choice, with its note.

Starting a lane is not here: launching belongs to the planned launcher
(D-swarm-pilot-2026-11-05). Nor is the fleet: family and trust come from the lane's template
and the model catalog.

A lane's family is the VENDOR: the ledger and an author's family are keyed by ``family_for``,
which answers ``anthropic`` where the catalog and a template say ``claude``. Passed through
raw, a template's family would miss every ledger cell and look independent of its own vendor
(R31e25aecff).
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ...config import Config
from ...core.admission import is_shared
from ...core.model import State
from ...core.schedule import dep_status
from .. import modelreg
from . import ledger as L
from . import score
from .score_config import Weights, lane_prices

if TYPE_CHECKING:
    from ..lanes.launch_template import Template

OFFER = "offer"
HOLD = "hold"
NONE = "none"
UNAVAILABLE = "unavailable"
CUT = "..."


@dataclass(frozen=True)
class Bounds:
    """How much an answer may say: the note's length and the refusals it lists."""

    note_chars: int
    why_items: int

    @classmethod
    def from_config(cls, cfg: Config) -> Bounds:
        return cls(cfg.swarm.dispatch_note_chars, cfg.swarm.dispatch_why_items)


@dataclass(frozen=True)
class Slots:
    """The limit in force, as the scheduler computed it. ``tree`` says the binding limit is the
    worktree cap (it is what left no candidate), not the flight limit."""

    in_flight: int
    limit: int
    paused: str = ""  # why admission is paused, "" when it is not
    tree: bool = False  # the limit is the worktree cap, told as such instead of "parallel"

    def held(self) -> str:
        """Why nothing may start now, or "" when a slot is free."""
        if self.paused:
            return f"admission paused: {self.paused}"
        if self.in_flight >= self.limit:
            word = "worktree" if self.tree else "parallel"
            return f"{word} limit reached: {self.in_flight} in flight of {self.limit}"
        return ""


@dataclass(frozen=True)
class LaneFacts:
    """What the model catalog says of a lane; "" or None is what it cannot vouch for."""

    model: str = ""
    vendor: str = ""
    family: str = ""  # the catalog's own view of the model, to catch a template that disagrees
    price_in: float | None = None
    price_out: float | None = None
    window: int | None = None


@dataclass(frozen=True)
class LaneSource:
    """A lane as its records describe it. ``trust_declared``: the template file names a tier
    (a ``Template`` reads 0 for one it omits). ``approved``: a person approved the template at
    its digest, without which its tier is only the file's own word."""

    template: Template | None
    facts: LaneFacts
    trust_declared: bool
    approved: bool
    live: int = 0
    cap: int = 1
    level: int = 0  # measured conformance level (0 = none), from the conformance store
    pools: tuple = ()  # endpoint pool headroom (capacity.view), from the capacity probe
    defect: str = ""  # T.load's note why a named template was rejected, "" for a plain miss


@dataclass(frozen=True)
class Answer:
    status: str
    lane: str
    item: str = ""
    note: str = ""
    why: tuple[str, ...] = ()
    decision: score.Decision | None = None


def catalog_facts(cat: modelreg.Catalog, t: Template) -> LaneFacts:
    """The catalog's facts for ``t``. The one reader here that is not clock-free: a fact past
    its review date stops being believed. The caller reads it, so the answer stays pure."""
    route = modelreg.presets(cat).get(t.route, {})
    model = t.model or route.get("model") or ""
    res = modelreg.resolve(cat, model)
    prices = lane_prices(cat, model, t.route) if model else {}
    return LaneFacts(
        model=model,
        vendor=res.vendor,
        family=res.family,
        price_in=prices.get("price_in"),
        price_out=prices.get("price_out"),
        window=prices.get("window"),
    )


def gaps(src: LaneSource) -> tuple[str, ...]:
    """What is unknown about a lane; empty means it can be scored. Nothing here is defaulted."""
    t, f = src.template, src.facts
    if t is None:
        if src.defect:
            return (f"no template: {src.defect}",)
        return ("no template: the lane's family and trust are unknown",)
    out: list[str] = []
    if not src.trust_declared:
        out.append(f"template {t.name} declares no trust tier")
    if not src.approved:
        out.append(f"template {t.name} is not approved at its digest: its trust is unvouched")
    if not f.model:
        out.append(f"template {t.name}: model unknown, none named and none served by the route")
    if not f.vendor:
        out.append(f"template {t.name}: the catalog knows no family for model {f.model!r}")
    elif f.family not in ("", modelreg.UNKNOWN) and t.family not in (f.family, f.vendor):
        out.append(f"template {t.name}: family {t.family!r} contradicts the catalog's {f.family!r}")
    return tuple(out)


def lane_of(name: str, src: LaneSource) -> score.Lane:
    """The scored lane for a source ``gaps`` found complete. A template's own price wins: a
    person approved it. The window is the catalog's."""
    t, f = src.template, src.facts
    if t is None:
        raise ValueError(f"lane {name} has no template: check `gaps` first")
    return score.Lane(
        name=name,
        family=f.vendor,
        model=f.model,
        trust=t.trust,
        price_in=f.price_in if t.price_in is None else t.price_in,
        price_out=f.price_out if t.price_out is None else t.price_out,
        window=f.window,
        local=t.local,
        live=src.live,
        cap=src.cap,
        kinds=t.kinds,
        level=src.level,
        pools=src.pools,
    )


def note(
    d: score.Decision, bounds: Bounds, *, role: str = "author", draw: float | None = None
) -> str:
    """The dispatch note: one line, the figures first, so a cut loses only the prose."""
    head = [
        f"dispatch item={d.item} lane={d.lane} family={d.family or '?'} "
        f"model={d.model or '?'} role={role} score={d.score:.3f}",
        *(f"{k}={v:.3f}" for k, v in d.parts),
    ]
    if draw is not None:
        head.append(f"draw={draw:.3f}")
    if d.reasons:
        head.append("why=" + "; ".join(d.reasons))
    text = " ".join(" ".join(head).split())
    if len(text) <= bounds.note_chars:
        return text
    cut = bounds.note_chars - len(CUT)
    return text[:cut] + CUT if cut > 0 else text[: bounds.note_chars]


def _bounded(rows: Sequence[str], bounds: Bounds) -> tuple[str, ...]:
    shown = tuple(rows[: bounds.why_items])
    left = len(rows) - len(shown)
    return (*shown, f"and {left} more") if left else shown


def pull_for(
    name: str,
    src: LaneSource,
    items: Sequence[score.Item],
    world: score.World,
    w: Weights,
    slots: Slots,
    bounds: Bounds,
    *,
    role: str = "author",
    author_family: str = "",
) -> Answer:
    """What the idle lane ``name`` should take now."""
    if missing := gaps(src):
        return Answer(UNAVAILABLE, name, why=missing)
    if held := slots.held():
        return Answer(HOLD, name, why=(held,))
    lane = lane_of(name, src)
    d = score.pull(lane, items, world, w, role=role, author_family=author_family)
    if d is not None:
        return Answer(OFFER, name, d.item, note(d, bounds, role=role), d.reasons, d)
    if not items:
        return Answer(NONE, name, why=("no item to offer",))
    rows = [
        f"{it.id}: "
        + "; ".join(score.refusals(it, lane, world, w, role=role, author_family=author_family))
        for it in sorted(items, key=lambda i: i.id)
    ]
    return Answer(NONE, name, why=_bounded(rows, bounds))


def place(
    item: score.Item,
    lanes: Sequence[score.Lane],
    world: score.World,
    w: Weights,
    slots: Slots,
    bounds: Bounds,
    *,
    draw: float | None = None,
    role: str = "author",
    author_family: str = "",
) -> Answer:
    """Which lane should take ``item`` now. ``draw`` is the caller's uniform draw, so an
    exploring pick replays from its note."""
    if held := slots.held():
        return Answer(HOLD, "", item.id, why=(held,))
    d = score.choose(item, lanes, world, w, draw=draw, role=role, author_family=author_family)
    if d is not None:
        text = note(d, bounds, role=role, draw=draw)
        return Answer(OFFER, d.lane, d.item, text, d.reasons, d)
    ranked = score.rank(item, lanes, world, w, role=role, author_family=author_family)
    rows = [f"{r.lane}: " + "; ".join(r.reasons) for r in ranked] or ["no lane to place it on"]
    return Answer(NONE, "", item.id, why=_bounded(rows, bounds))


def _own(globs: Sequence[str], shared: Sequence[str]) -> tuple[str, ...]:
    """``globs`` without the shared ones: a shared path overlaps nothing (D-shared-globs)."""
    return tuple(g for g in globs if not is_shared(g, list(shared)))


def item_of(
    state: State,
    item: str,
    *,
    chain: Collection[str],
    waited: Mapping[str, float],
    shared: Sequence[str],
) -> score.Item:
    """The scored item for a queue item. ``chain`` is the critical path, ``waited`` the seconds
    since the item was filed (the queue's wait, not how long it has stood ready), and its kind
    the one the ledger credits."""
    it = state.items[item]
    return score.Item(
        id=it.id,
        kind=L._kind(state, it.id, it.kind),
        globs=_own(it.globs, shared),
        needs=tuple(it.needs),
        critical=1.0 if it.id in chain else 0.0,
        waited_s=waited.get(it.id, 0.0),
    )


def world_of(
    state: State,
    items: Sequence[score.Item],
    *,
    cfg: Config,
    now: float,
    ledger: L.Ledger,
    families: Mapping[str, str],
    shared: Sequence[str],
    reviewer_families: tuple[str, ...] | None = None,
) -> score.World:
    """The world for ``items``: live leases by id, and the needs ``dep_status`` calls
    satisfied, so the scheduler's verdict on a dependency is the only one. ``families`` is
    item -> the family of its lease's model; an item not in it has no known family."""
    leases = state.active_leases(now, cfg.lease.grace_s)
    return score.World(
        done=frozenset(n for it in items for n in it.needs if dep_status(state, n, cfg)[0]),
        live=tuple(
            score.Live(i, _own(lz.globs, shared), families.get(i, ""))
            for i, lz in sorted(leases.items())
        ),
        ledger=ledger,
        reviewer_families=reviewer_families,
    )
