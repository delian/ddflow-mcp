"""`recover`: crashed agents' leases and orphaned worktrees, and the recovery plan.

The pure triage is `core.recovery_plan`; `services.recovery` applies the SAFE_AUTO half a
plan triaged. Moved out of `health.py` (B92f2fe5750): that module grew past the module-split
cap, and `recover` (with the recovery-plan imports it alone needs) is one whole area.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from ...core import outcome as O
from ...core import recovery_plan as RP
from ...core.plain import plain
from ...infra.worktree import absolutise
from ...services import jobs as JOBS
from ...services import leases as L
from ...services import mergequeue as MQ
from ...services import recovery as SR
from ...services.gates import running as RUN
from ...views.markdown import may_hold_work
from .._base import _load


def recover(
    repo: Path,
    *,
    item: str = "",
    apply: bool = False,
    all: bool = False,
    plan: bool = False,
    agent: str = "",
) -> O.Outcome:
    """Crashed agents' leases and orphaned worktrees. Exit 2 when there is nothing.

    A worktree with uncommitted changes is reported and never touched, whatever `apply`
    says — the whole value of the sweep is that it does not destroy the work it found.

    With `all` or `plan` this is the recovery plan (DX-recovery-plan): the pure triage
    in `core.recovery_plan` runs over the lease sweep AND the orphaned gate-run markers,
    the unended jobs and the queue entries a dead integrator left running. `plan` is the
    dry run (per item: SAFE_AUTO/PROPOSE/OPERATOR, the evidence, the exact commands);
    `apply` runs only SAFE_AUTO and refuses (exit 3) while the kill switch is on.
    """

    why_items = (
        "expired leases, orphan worktrees, dead gate runs, unended jobs, orphaned queue entries"
    )
    if not (all or plan):
        log, cfg, _ = _load(repo, agent)
        found = [r for r in L.sweep(log, cfg, repo, apply=apply) if not item or r.item == item]
        # Every entry that may hold work, as the brief bands them: a tree git could not
        # measure and a RUNNING item nobody holds count too (B3f8c406fea, Be0d308babd).
        salvageable = [r for r in found if may_hold_work(r)]
        data: dict[str, Any] = {
            "found": [absolutise(repo, plain(r)) for r in found],
            "count": len(found),
            "salvageable": len(salvageable),
            "applied": apply,
            "_render": {"found": found, "salvageable": salvageable},
        }
        if not found:
            return O.nothing(
                "recover", "Nothing to recover — no expired leases, no orphan worktrees.", **data
            )
        return O.ok("recover", **data)

    log, cfg, st = _load(repo, agent)
    recoveries = [r for r in L.scan(log, cfg, repo, state=st) if not item or r.item == item]
    dead = [plain(m) for m in RUN.dead_runs(repo, item)]
    jobs = [r for r in SR.job_rows(st, JOBS) if not item or r["item"] == item]
    queue = [
        e.item for e in MQ.entries(repo) if e.state == MQ.RUNNING and (not item or e.item == item)
    ]
    facts = RP.Facts(
        recoveries=tuple(plain(r) for r in recoveries),
        dead=tuple(dead),
        jobs=tuple(jobs),
        queue=tuple(queue),
        attempts=SR.attempt_counts(log),
        kill_switch=SR.kill_switch(repo, cfg),
    )
    actions = RP.triage(facts, time.time(), RP.Limits())
    salvageable = [r for r in recoveries if may_hold_work(r)]

    if plan:
        data = {
            "found": [a.as_dict() for a in actions],
            "count": len(actions),
            "classes": {c: sum(1 for a in actions if a.cls == c) for c in RP.CLASSES},
            "salvageable": len(salvageable),
            "applied": False,
            "paused": facts.kill_switch,
            "_render": {"plan": True, "actions": actions, "recoveries": recoveries},
        }
        if not actions:
            return O.nothing("recover", f"Nothing to recover — no {why_items}.", **data)
        return O.ok("recover", **data)

    if not (recoveries or dead or jobs or queue):
        return O.nothing("recover", f"Nothing to recover — no {why_items}.")
    if apply and facts.kill_switch:
        return O.refused(
            "recover",
            "the recovery kill switch is on (`.ddflow/local/recovery.stop`); nothing ran. "
            "The plan stays visible: `ddflow recover --all --plan`.",
            paused=True,
            plan_actions=[a.as_dict() for a in actions],
        )
    applied_rows = SR.apply(repo, log, agent, actions, cfg=cfg) if apply else []
    failures = [r for r in applied_rows if r["note"].startswith("failed:")]
    data: dict[str, Any] = {
        "found": [absolutise(repo, plain(r)) for r in recoveries],
        "count": len(recoveries),
        "salvageable": len(salvageable),
        "applied": apply,
        "plan_actions": [a.as_dict() for a in actions],
        "applied_rows": applied_rows,
        "paused": facts.kill_switch,
        "_render": {"found": recoveries, "salvageable": salvageable, "applied_rows": applied_rows},
    }
    if failures:
        return O.failed(
            "recover",
            f"{len(failures)} recovery step(s) failed while applying: "
            + "; ".join(f"{r['item']}: {r['note']}" for r in failures[:4]),
            **data,
        )
    return O.ok("recover", **data)
