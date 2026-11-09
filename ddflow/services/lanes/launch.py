"""Start a swarm lane from an approved template (SW-launch, D-swarm-answers Q2, Q6).

One call, in a fixed order, and a refusal at any step starts nothing:

1. ``[swarm] launch_enabled``; 2. the template is valid and within ``launch_max_budget_usd``;
3. a PERSON approved the template at its current digest; 4. the scheduler's hard constraints
(``score.refusals``: needs, glob overlap, ``max_lanes``, ``family_cap``, trust, independence)
and the estimated cost against the template's budget; 5. the Tier-0 seed pack
(``seedpack.Knobs.from_config`` and ``from_state``) becomes the lane's prompt; 6. the opencode/kilo
lane is prepared in its private home (the plugin's own consent is asked there) and its hook level
must reach the template's ``min_hooks``; 7. the Lane contract's ``start``; 8. one audit note.

No daemon: the scheduler tick calls this. No model is reached here; ``binary`` and the driver are
injectable so a test runs a stub. Approval is only ever read (``launch_template.approved``).
"""

from __future__ import annotations

import contextlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ...config import Config
from .. import modelreg
from ..lane_driver import LaneHandle, Result, driver_for
from ..swarm import conformance_io as CIO
from ..swarm import dispatch as D
from ..swarm import lanehooks as LH
from ..swarm import score
from ..swarm import seedpack as SP
from ..swarm.score_config import Weights
from . import capacity as CAPACITY
from . import endpoint as EP
from . import launch_template as T
from . import opencode as OC

NOTE_SOURCE = "lane-launch"


@dataclass(frozen=True)
class Refused:
    """Nothing was started. ``consent`` is set when a person's approval is what is missing."""

    reasons: tuple[str, ...]
    consent: T.Consent | LH.Consent | None = None


@dataclass(frozen=True)
class Launched:
    template: T.Template
    handle: LaneHandle
    prep: OC.LanePrep
    driver: Any
    decision_note: str
    seed_dropped: tuple[str, ...] = ()
    slots: contextlib.ExitStack = field(default_factory=contextlib.ExitStack, repr=False)
    #: the endpoint slots held for the lane's lifetime: open while the lane runs. The caller
    #: closes it (``Launched.release()``) after the lane stops, which frees the endpoint for
    #: the next lane; nothing here closes it and it never releases on garbage collection.

    def release(self) -> None:
        """Free the held endpoint slots (idempotent). Call after the lane stops and its
        cleanup ends, so the endpoint can admit the next lane."""
        self.slots.close()


@dataclass(frozen=True)
class Request:
    template: T.Template
    item: str
    agent: str  #: the lane's own identity
    repo: Path
    worktree: Path
    home: Path
    state: Any  #: folded event log (approvals, item, decisions)
    cat: modelreg.Catalog
    world: score.World  #: the scheduler's snapshot of live lanes: the caps are judged on it
    cfg: Config = field(default_factory=Config)
    est_in: int = 0
    est_out: int = 0
    handoff: str = ""
    handoff_by: str = ""
    binary: OC.Binary | None = None
    log: Any = None  #: an event log to write the audit note to; None writes none


def _check_template(t: T.Template, cfg: Config) -> list[str]:
    out = T.problems(t)
    cap = cfg.swarm.launch_max_budget_usd
    if cap > 0 and t.budget_usd > cap:
        out.append(f"template budget_usd {t.budget_usd} is above launch_max_budget_usd {cap}")
    return out


def conformance_level(
    repo: Path, agent: str, *, max_age_s: int = 0, now: float | None = None
) -> int:
    """The lane's measured conformance level, 0 when no run has graded it yet or its report
    is older than ``max_age_s`` (``conformance.max_age_s``: an expired run admits at no level).
    ``now`` defaults to the wall clock."""
    report = CIO.load(repo, agent)
    if report is None:
        return 0
    at = time.time() if now is None else now
    if max_age_s and at - report.at > max_age_s:
        return 0
    return report.level


def resolved_pools(cat: modelreg.Catalog, route: str, model: str, default_slots: int) -> tuple:
    """The endpoint `Pool` objects a lane on ``route`` takes (its own pool only; task
    ``--resources`` pools are the task's, not a lane's). Empty when the route is unreadable."""
    ep = EP.resolve(cat, route, model, require_tools=False)
    if isinstance(ep, EP.Result):
        return ()
    return CAPACITY.pools_for(ep, default_slots, {})


def endpoint_pools(
    cat: modelreg.Catalog,
    route: str,
    model: str,
    default_slots: int,
    directory: Path | str | None = None,
) -> tuple:
    """The endpoint pool headroom a lane on ``route`` needs now: the route's ``concurrency``
    (or ``default_slots``) against the slots busy in ``directory`` (the host slot dir when
    None). Empty when the route cannot be read."""
    pools = resolved_pools(cat, route, model, default_slots)
    return CAPACITY.view(pools, CAPACITY.busy_counts(pools, directory)) if pools else ()


def _constraints(req: Request, w: Weights) -> list[str]:
    """The scheduler's hard constraints, judged on the same lane as dispatch builds it.

    The lane is built through `dispatch.lane_of`, so launch scores under the catalog's
    VENDOR (``family_for``), not the template's family id (B60ff757d13), and enforces the
    catalog's window: a lane whose token estimate does not fit its served window is refused
    here exactly as dispatch would refuse it for the next item. The template's own price is
    the budget's source (a person approved it); the catalog price ``lane_of`` falls back to
    is a dispatch term, not the launch budget."""
    t, item = req.template, req.state.items[req.item]
    sc_item = score.Item(
        id=item.id,
        kind=getattr(item, "kind", "task") or "task",
        globs=tuple(item.globs),
        needs=tuple(item.needs),
        est_in=req.est_in,
        est_out=req.est_out,
    )
    lane = D.lane_of(
        req.agent,
        D.LaneSource(
            t,
            D.catalog_facts(req.cat, t),
            True,  # trust_declared: a trust tier only gates `gaps`, not the lane itself
            True,  # approved: launch() already checked T.approved before the constraints
            level=conformance_level(
                req.repo, req.agent, max_age_s=req.cfg.swarm.conformance_max_age_s
            ),
            pools=endpoint_pools(req.cat, t.route, t.model, req.cfg.lanes.endpoint_slots),
        ),
    )
    out = score.refusals(sc_item, lane, req.world, w)
    if not t.local and (t.price_in is None or t.price_out is None):
        out.append("the budget cannot be enforced: the template has no verified price")
    elif (
        (cost := score.est_cost(sc_item, lane)) is not None and cost > t.budget_usd and not t.local
    ):
        out.append(f"estimated cost {cost:.4f} USD is above the template budget {t.budget_usd}")
    return out


def _start(req: Request, t: T.Template, drv: Any, spec: Any) -> tuple[Any, contextlib.ExitStack]:
    """Hold the endpoint's slots for the lane's lifetime then start it. The returned stack
    stays OPEN (the slots live until the caller closes it after the lane stops); TimeoutError
    when the endpoint pool cannot fit the lane before ``slot_wait_s``. A start failure beyond
    the wait still releases the slots taken before it propagates."""
    slots = contextlib.ExitStack()
    slots.enter_context(
        CAPACITY.acquire(
            resolved_pools(req.cat, t.route, t.model, req.cfg.lanes.endpoint_slots),
            req.cfg.lanes.slot_wait_s,
        )
    )
    try:
        return drv.start(spec), slots
    except BaseException:
        slots.close()
        raise


def _audit(req: Request, text: str) -> None:
    if req.log is not None:
        req.log.append(
            "session.note", req.item, {"text": text, "item": req.item, "source": NOTE_SOURCE}
        )


def launch(req: Request) -> Launched | Refused:
    t, cfg = req.template, req.cfg
    if not cfg.swarm.launch_enabled:
        return Refused(("lane launching is off: set [swarm] launch_enabled",))
    if bad := _check_template(t, cfg):
        return Refused(tuple(f"template {t.name}: {b}" for b in bad))
    verdict = T.approved(req.state, t)
    if not verdict.ok:
        return Refused((verdict.reason,), T.consent(t))
    if req.item not in req.state.items:
        return Refused((f"no such item {req.item!r}",))
    if why := _constraints(req, Weights.from_config(cfg)):
        return Refused(tuple(why))
    binary = req.binary if req.binary is not None else OC.select((t.harness,))
    if isinstance(binary, Result):
        return Refused((f"{binary.status}: {binary.detail}",))
    if binary.name != t.harness:
        return Refused(
            (f"binary {binary.name!r} is not the approved template's harness {t.harness!r}",)
        )
    seed = SP.build(
        SP.from_state(
            req.state, req.repo, req.item, handoff=req.handoff, handoff_by=req.handoff_by
        ),
        SP.Knobs.from_config(cfg),
    )
    # A route whose tool_call is only `unverified` must still launch: the lane IS the
    # live probe that verifies it (Bc64f9048f5). The slot checks above already resolve
    # the endpoint with require_tools=False; prepare() must not refuse the same route on
    # its require_tools=True default, or the first probe has no route to run on at all.
    prep = OC.prepare(
        req.cat,
        t.route,
        repo=req.repo,
        worktree=req.worktree,
        home=req.home,
        agent=req.agent,
        prompt=SP.render(seed),
        item=req.item,
        state=req.state,
        model=t.model,
        binary=binary,
        require_tools=False,
        cfg=cfg,
    )
    if isinstance(prep, LH.Consent):
        OC.cleanup(req.home)
        return Refused((f"the lane's hook plugin needs a person's approval: {prep.how}",), prep)
    if isinstance(prep, Result):
        OC.cleanup(req.home)
        return Refused((f"{prep.status}: {prep.detail}",))
    if T.HOOK_LEVELS.index(prep.level) < T.HOOK_LEVELS.index(t.min_hooks):
        OC.cleanup(req.home)
        return Refused(
            (f"hook level {prep.level!r} is below the template's min_hooks {t.min_hooks!r}",)
        )
    drv = driver_for(OC.TRANSPORT)
    if isinstance(drv, Result):
        OC.cleanup(req.home)
        return Refused((f"{drv.status}: {drv.detail}",))
    # hold the endpoint's slots for the lane's lifetime: the slot locks the scheduler probes
    # are held while the lane runs (until the caller closes ``Launched.slots`` after the lane
    # stops), so a full endpoint refuses the next lane instead of oversubscribing it. An
    # unreadable route has no pool to hold.
    try:
        h, slots = _start(req, t, drv, prep.spec)
    except TimeoutError as e:  # the endpoint pool could not fit this lane before the wait
        OC.cleanup(req.home)
        return Refused((f"endpoint slot: {e}",))
    if isinstance(h, Result):
        slots.close()
        OC.cleanup(req.home)
        return Refused((f"{h.status}: {h.detail}",))
    note = json.dumps(
        {
            "template": t.name,
            "digest": t.digest(),
            "harness": f"{prep.binary.name} {prep.binary.version or 'unread'}",
            "family": t.family,
            "agent": req.agent,
            "hooks": prep.level,
            "budget_usd": t.budget_usd,
            "seed_dropped": list(seed.dropped),
        },
        sort_keys=True,
    )
    try:
        _audit(req, f"lane.launched {note}")
    except Exception as e:  # the lane runs: report it, never lose the handle
        note = json.dumps({**json.loads(note), "audit_error": type(e).__name__}, sort_keys=True)
    return Launched(t, h, prep, drv, note, seed.dropped, slots)
