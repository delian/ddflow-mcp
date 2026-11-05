"""`ddflow next --lane`: read what the pull answer needs, then ask it (SW-pull).

A read verb (r-read-verbs-read-only): it appends nothing. The choice is
`services.swarm.dispatch`; the dispatch note comes back as text and `claim --note` records it.
"""

from __future__ import annotations

import time
import tomllib
from pathlib import Path
from typing import Any

from ...config import Config
from ...config_sections.agent import family_for
from ...core import outcome as O
from ...core import progress as PR
from ...core.admission import shared_globs
from ...core.defaults import DEFAULT_NEXT_KIND
from ...core.model import RUNNING, State
from ...core.schedule import Caps, caps, critical_path, open_slots
from ...services import flowstate as FL
from ...services import modelreg
from ...services.lanes import launch_template as T
from ...services.swarm import dispatch as D
from ...services.swarm import ledger as LG
from ...services.swarm import score
from ...services.swarm.score_config import Weights
from .._base import _load
from ..model_cmds import LOCAL
from .planning import plan_for
from .ready import _unknown_phase


def _declares_trust(path: Path, name: str) -> bool:
    """Does the template's own table name a tier? A `Template` reads 0 for one it omits, and
    a tier nobody wrote is unknown, not the lowest."""
    try:
        tables = tomllib.loads(path.read_text("utf-8")).get("template", {})
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return False
    row = tables.get(name) if isinstance(tables, dict) else None
    return isinstance(row, dict) and "trust" in row


def _source(repo: Path, cfg: Config, st: State, lane: str, now: float) -> D.LaneSource:
    path = Path(repo) / cfg.swarm.launch_templates_file
    t = T.load(path)[0].get(lane)
    live = sum(lz.holder == lane for lz in st.active_leases(now, cfg.lease.grace_s).values())
    if t is None:
        return D.LaneSource(None, D.LaneFacts(), False, False, live=live)
    return D.LaneSource(
        t,
        D.catalog_facts(modelreg.load(local_dir=Path(repo) / LOCAL), t),
        _declares_trust(path, lane),
        T.approved(st, t).ok,
        live=live,
    )


def _slots(c: Caps, reason: str) -> D.Slots:
    """The tighter of the two caps, as the pull answer judges it."""
    paused = reason if c.paused else ""
    if c.tree_slots < c.flight_slots:
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


def _candidates(p: Any, st: State, cfg: Config, c: Caps, live: dict[str, Any]) -> list[str]:
    """Everything ready in every sense but the offer: the cap-held and the items overlapping
    another offer compete too, since one lane takes one item and `Slots` already judged the
    cap. An interrupted item does not: its worktree may hold work, and someone salvages that
    before a lane starts over. With only reserved slots left, only reserved work competes."""
    ids = [*(i.id for i in p.ready), *p.capped, *p.overlapped]
    ids = [i for i in ids if st.items[i].state != RUNNING]
    free, reserved = open_slots(st, cfg, live, c.live_items, c.limit, c.flight_slots, c.tree_slots)
    shares = (free.ordinary_flight, free.ordinary_tree)
    if any(share is not None and share <= 0 for share in shares):
        return [i for i in ids if i in reserved]
    return ids


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
    events = log.read_all()
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
    return D.pull_for(lane, src, items, world, w, slots, bounds)


def next_lane(
    repo: Path, *, lane: str, kind: str = DEFAULT_NEXT_KIND, phase: str = "", agent: str = ""
) -> O.Outcome:
    """The pull answer for ``lane``: an offer (exit 0), else exit 2 with why not."""
    log, cfg, st = _load(repo, agent)
    if unknown := _unknown_phase(st, phase):
        return O.failed("next", unknown, phase=phase)
    now = time.time()
    me = cfg.agent.id or log.agent_id
    p = plan_for(repo, log, cfg, st, purpose="offer", kind=kind, phase=phase, agent=me)
    parallel = FL.limit_for(repo, cfg, st, log.read_all)
    live = st.active_leases(now, cfg.lease.grace_s)
    c = caps(st, cfg, live, parallel)
    a = _ask(
        repo,
        log,
        cfg,
        st,
        _source(repo, cfg, st, lane, now),
        lane,
        _candidates(p, st, cfg, c, live),
        slots=_slots(c, parallel.reason),
        phase=phase,
        now=now,
    )
    data = {"lane": a.lane, "status": a.status, "item": a.item, "note": a.note, "why": list(a.why)}
    if a.status == D.OFFER:
        return O.ok("next", **data)
    return O.nothing("next", "; ".join(a.why), **data)
