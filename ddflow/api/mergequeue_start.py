"""Start the integrator: the answer to "who runs `queue run`?" (LC-integrator-trigger).

A released item has no lease and no owner, so something must make sure the integrator runs.
There is no daemon: accepting a release starts ONE detached integrator through the job
machinery (`job_detach`: its own session, a log path, a recorded job), and a tick that rides on
any ddflow command starts it again when an entry has waited too long with nobody integrating.
Single flight: a short lock orders the check and the launch, a running integrator job (or the
queue's own lock holder) means nothing is started, and the queue's lock still lets only one
integrator integrate whatever happens here."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

from ..core import outcome as O
from ..infra import fsio
from ..services import lanerelease as LR
from ..services import mergequeue as MQ
from ..services.ticks import TickCtx
from ._base import _load
from .jobs import job_detach

#: Seconds the start lock is waited for: it is held for one check and one launch.
START_LOCK_WAIT_S = 10.0

Starter = Callable[..., O.Outcome]


def _not_started(why: str, **more) -> dict:
    return {"started": False, "reason": why, **more}


def start_integrator(
    repo: Path, *, agent: str = "", starter: Starter | None = None, now: float | None = None
) -> dict:
    """Start one detached integrator unless one is alive, the knob is off or nothing waits.
    Returns ``{"started", "reason", "job", "log"}``; never raises on a refusal."""
    _log, cfg, _st = _load(repo, agent)
    if cfg.merge_queue.auto_run != "on":
        return _not_started(
            f"[merge_queue].auto_run = off: run `{LR.RUN_CLI}` (MCP: `{LR.RUN_MCP}`) yourself"
        )
    lock = repo / MQ.QUEUE_DIR / "start.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        with fsio.file_lock(lock, START_LOCK_WAIT_S):
            return _start_locked(repo, agent, starter or job_detach)
    except fsio.LockTimeout:
        return _not_started("another start is in progress; it will run the integrator")


def _start_locked(repo: Path, agent: str, starter: Starter) -> dict:
    _l, cfg, st = _load(repo, agent)  # read under the lock: a start just finished is seen
    if cfg.merge_queue.auto_run != "on":
        return _not_started("[merge_queue].auto_run was turned off")
    waiting = [e for e in MQ.live(repo) if e.state == MQ.QUEUED]
    if not waiting:
        return _not_started("nothing is waiting in the merge queue")
    alive = LR.live_jobs(repo, st)
    if len(alive) >= cfg.merge_queue.auto_run_max_parallel or MQ.holder(repo) is not None:
        return _not_started("an integrator is already running", job=alive[0].id if alive else "")
    out = starter(repo, waiting[0].item, LR.integrator_command(repo), cwd=str(repo), agent=agent)
    if out.exit != O.OK:
        return _not_started(
            f"could not start the integrator: {out.reason}. Run `{LR.RUN_CLI}` "
            f"(MCP: `{LR.RUN_MCP}`) yourself"
        )
    return {"started": True, "reason": "", "job": out.data["id"], "log": out.data["log"]}


def integrator_tick(ctx: TickCtx) -> str | None:
    """The tick: start the integrator when an entry has waited past
    ``[merge_queue].auto_run_after_s`` and nobody is integrating."""
    if not LR.overdue(Path(ctx.repo), ctx.cfg, time.time()):
        return None
    got = start_integrator(Path(ctx.repo))
    return f"started integrator job {got['job']}" if got["started"] else got["reason"]
