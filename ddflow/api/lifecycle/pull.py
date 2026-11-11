"""`ddflow next --lane`: read what the pull answer needs, then ask it (SW-pull).

A read verb (r-read-verbs-read-only): it appends nothing. The choice is
`services.swarm.dispatch`; the dispatch note comes back as text and `claim --note` records it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...adapters.clock import DEFAULT as CLOCK
from ...adapters.toml import TomlError, TomlLibIo
from ...config import Config
from ...config_sections.agent import family_for
from ...core import outcome as O
from ...core import progress as PR
from ...core.admission import shared_globs
from ...core.defaults import DEFAULT_NEXT_KIND
from ...core.model import RUNNING, State
from ...core.schedule import Caps, caps, critical_path, needs_tree, open_slots
from ...infra import git as G
from ...services import flowstate as FL
from ...services import modelreg
from ...services.lanes import launch as LN
from ...services.lanes import launch_template as T
from ...services.swarm import dispatch as D
from ...services.swarm import ledger as LG
from ...services.swarm import score
from ...services.swarm.score_config import Weights
from ...services.ticks import TickCtx
from .._base import _load
from ..model_cmds import LOCAL
from .planning import plan_for
from .ready import _unknown_phase


def _declares_trust(path: Path, name: str) -> bool:
    """Does the template's own table name a tier? A `Template` reads 0 for one it omits, and
    a tier nobody wrote is unknown, not the lowest."""
    try:
        tables = TomlLibIo().read(path).get("template", {})
    except (OSError, TomlError):
        return False
    row = tables.get(name) if isinstance(tables, dict) else None
    return isinstance(row, dict) and "trust" in row


def _source(repo: Path, cfg: Config, st: State, lane: str, now: float) -> D.LaneSource:
    path = Path(repo) / cfg.swarm.launch_templates_file
    t = T.load(path)[0].get(lane)
    live = sum(lz.holder == lane for lz in st.active_leases(now, cfg.lease.grace_s).values())
    if t is None:
        return D.LaneSource(None, D.LaneFacts(), False, False, live=live)
    cat = modelreg.load(local_dir=Path(repo) / LOCAL)
    return D.LaneSource(
        t,
        D.catalog_facts(cat, t),
        _declares_trust(path, lane),
        T.approved(st, t).ok,
        live=live,
        level=LN.conformance_level(repo, lane, max_age_s=cfg.swarm.conformance_max_age_s, now=now),
        pools=LN.endpoint_pools(cat, t.route, t.model, cfg.lanes.endpoint_slots),
    )


def _slots(c: Caps, reason: str, ids: list[str]) -> D.Slots:
    """The cap the pull answer judges: the item limit, or the worktree limit when that is
    what left no candidate (an item with no tree of its own is not held by it)."""
    paused = reason if c.paused else ""
    if not ids and c.tree_slots < c.flight_slots:
        return D.Slots(c.with_trees, c.tree_limit, paused)
    return D.Slots(c.in_flight, c.limit, paused)


def _waited(events: Any, ids: set[str], now: float) -> dict[str, float]:
    """Seconds since each item was filed: the queue term's measure of how long it has waited."""
    out: dict[str, float] = {}
    for ev in events:
        if ev.subject in ids and ev.subject not in out:
            out[ev.subject] = max(0.0, now - PR.epoch(ev.ts))
    return out


def _live_families(events: Any, cfg: Config, live: dict[str, Any], now: float) -> dict[str, str]:
    """Item -> the family of whoever holds it now, told as the ledger tells an author's: the
    lease's own model, else the holder's latest session. "" when neither says."""
    sessions, leases = LG._models(events), LG._lease_models(events)
    fams = dict(getattr(cfg.agent, "families", {}) or {}) or None
    return {
        i: family_for(LG._author_model(i, lz.holder, now, sessions, leases), fams)
        for i, lz in live.items()
    }


def _fits(it: Any, free: Any, reserved: frozenset[str]) -> bool:
    """Is there a slot this item may take? Reserved work may take any free slot, ordinary
    work its share, and only an item that takes a worktree needs a tree slot."""
    mine = it.id in reserved or free.ordinary_flight is None
    flight = free.flight if mine else free.ordinary_flight
    tree = free.tree if mine else free.ordinary_tree
    return flight > 0 and (tree > 0 or not needs_tree(it))


def _candidates(p: Any, st: State, cfg: Config, c: Caps, live: dict[str, Any]) -> list[str]:
    """Everything ready in every sense but the offer: the cap-held and the items overlapping
    another offer compete too, since one lane takes one item, so long as a slot fits them. An
    interrupted item does not: its worktree may hold work, and someone salvages that before a
    lane starts over."""
    ids = [*(i.id for i in p.ready), *p.capped, *p.overlapped]
    free, reserved = open_slots(st, cfg, live, c.live_items, c.limit, c.flight_slots, c.tree_slots)
    return [i for i in ids if st.items[i].state != RUNNING and _fits(st.items[i], free, reserved)]


def _ask(
    repo: Path,
    log: Any,
    cfg: Config,
    st: State,
    src: D.LaneSource,
    lane: str,
    ids: list[str],
    *,
    slots: D.Slots,
    phase: str,
    now: float,
) -> D.Answer:
    w, bounds = Weights.from_config(cfg), D.Bounds.from_config(cfg)
    if D.gaps(src) or slots.held():  # no item is offered: do not fold the whole log for it
        return D.pull_for(lane, src, (), score.World(), w, slots, bounds)
    world, items = _world_and_items(log.read_all(), cfg, st, ids, phase, now)
    return D.pull_for(lane, src, items, world, w, slots, bounds)


def next_lane(
    repo: Path,
    *,
    lane: str,
    kind: str = DEFAULT_NEXT_KIND,
    phase: str = "",
    agent: str = "",
    phase_only: bool = False,
) -> O.Outcome:
    """The pull answer for ``lane``: an offer (exit 0), else exit 2 with why not."""
    log, cfg, st = _load(repo, agent)
    if unknown := _unknown_phase(st, phase):
        return O.failed("next", unknown, phase=phase)
    now = CLOCK.wall()
    me = cfg.agent.id or log.agent_id
    p = plan_for(
        repo, log, cfg, st, purpose="offer", kind=kind, phase=phase, agent=me, phase_only=phase_only
    )
    parallel = FL.limit_for(repo, cfg, st, log.read_all)
    live = st.active_leases(now, cfg.lease.grace_s)
    c = caps(st, cfg, live, parallel)
    ids = _candidates(p, st, cfg, c, live)
    a = _ask(
        repo,
        log,
        cfg,
        st,
        _source(repo, cfg, st, lane, now),
        lane,
        ids,
        slots=_slots(c, parallel.reason, ids),
        phase=phase,
        now=now,
    )
    data = {"lane": a.lane, "status": a.status, "item": a.item, "note": a.note, "why": list(a.why)}
    if a.status == D.OFFER:
        return O.ok("next", **data)
    return O.nothing("next", "; ".join(a.why), **data)


def _world_and_items(events: Any, cfg: Config, st: State, ids: list[str], phase: str, now: float):
    """The scored items and scheduler world for ``ids`` -- the one fold `_ask` and `_launch_due`
    share, so the launch constraints judge the exact snapshot the pull offered (one answer)."""
    w = Weights.from_config(cfg)
    shared = shared_globs(cfg)
    chain = critical_path(st, phase, cfg)
    waited = _waited(events, set(ids), now)
    items = [D.item_of(st, i, chain=chain, waited=waited, shared=shared) for i in ids]
    live = st.active_leases(now, cfg.lease.grace_s)
    world = D.world_of(
        st,
        items,
        cfg=cfg,
        now=now,
        ledger=LG.build(events, st, cfg, now, bug_window_s=w.bug_window_s),
        families=_live_families(events, cfg, live, now),
        shared=shared,
    )
    return world, items


def _lane_home(repo: Path, name: str) -> Path:
    """A lane's private home: under the git-ignored `.ddflow/local`, so its hook files and
    provider block never reach the project (and `launch` refuses any home inside a checked
    root)."""
    return Path(repo) / ".ddflow" / "local" / "lanes" / name


def _lane_worktree(repo: Path, name: str) -> Path | None:
    """The lane's own linked worktree, created once and reused. None when it cannot be
    created (a corrupt leftover): the caller reports the refusal, never guesses a shared one."""
    wt = Path(repo) / ".ddflow" / "worktrees" / name
    if not (wt / ".git").exists():
        if not G.run(repo, "worktree", "add", "-q", "--detach", str(wt)).ok:
            return None
    return wt


def _launch_due(ctx: TickCtx) -> str | None:
    """The swarm scheduler tick: the production caller of `lanes.launch.launch` (B5c8139e564).

    Reads the pull answer for each approved template lane that holds no item, and starts the
    first one the same `launch` a test calls directly -- so every guard (template approval,
    caps, budget, hook consent, hook level) is the one guard, and a refusal starts nothing.
    At most one lane starts per pass; the next pass starts the next. A lane that is refused
    (or whose worktree cannot be made) never blocks the lane after it: the loop moves on, so
    a sticky refusal on one template cannot stall every other. Returns a one-line note or
    ``None`` when nothing started or is due."""
    repo = Path(ctx.repo)
    log, cfg, st = _load(repo)
    if not cfg.swarm.launch_enabled:
        return None
    now = CLOCK.wall()
    templates, _notes = T.load(Path(repo) / cfg.swarm.launch_templates_file)
    if not templates:
        return None
    live = st.active_leases(now, cfg.lease.grace_s)
    me = cfg.agent.id or log.agent_id
    p = plan_for(repo, log, cfg, st, purpose="offer", kind=DEFAULT_NEXT_KIND, phase="", agent=me)
    parallel = FL.limit_for(repo, cfg, st, log.read_all)
    c = caps(st, cfg, live, parallel)
    ids = _candidates(p, st, cfg, c, live)
    w = Weights.from_config(cfg)
    bounds = D.Bounds.from_config(cfg)
    slots = _slots(c, parallel.reason, ids)
    world, items = _world_and_items(log.read_all(), cfg, st, ids, "", now)
    cat = modelreg.load(local_dir=Path(repo) / LOCAL)
    first_refusal = ""
    for name in sorted(templates):
        if any(lz.holder == name for lz in live.values()):
            continue  # that lane is already working an item
        src = _source(repo, cfg, st, name, now)
        if D.gaps(src):
            continue  # unavailable: family/trust/model unvouched; nothing is defaulted
        a = D.pull_for(name, src, items, world, w, slots, bounds)
        if a.status != D.OFFER:
            continue  # a lane not offered or held now must not stop the one that is
        worktree = _lane_worktree(repo, name)
        if worktree is None:
            first_refusal = first_refusal or f"lane {name!r}: could not create its worktree"
            continue
        req = LN.Request(
            template=src.template,
            item=a.item,
            agent=name,
            repo=Path(repo),
            worktree=worktree,
            home=_lane_home(repo, name),
            state=st,
            cat=cat,
            world=world,
            cfg=cfg,
            log=log,
        )
        r = LN.launch(req)
        if isinstance(r, LN.Launched):
            note = f"launched lane {name!r} on {a.item}"
            return f"{note}; also refused: {first_refusal}" if first_refusal else note
        first_refusal = first_refusal or (
            r.reasons[0] if r.reasons else f"lane {name!r} was refused"
        )
    return first_refusal or None
