"""Workflow state overview: configuration, rules, decisions, active work, queue and bugs."""

from __future__ import annotations

import re
import time
from pathlib import Path

from ..core import outcome as O
from ..core.schedule import plan
from ._base import _load

_READY_SHOWN, _BLOCKED_SHOWN, _BUGS_SHOWN = 10, 5, 5
_SEVERITY = {"critical": 0, "high": 1, "medium": 2, "low": 3, "": 4}


def workflow_state(repo: Path, agent: str = "") -> O.Outcome:
    """One read-only overview: workflow, rules, decisions, active work, queue, bugs."""
    log, cfg, st = _load(repo, agent)
    p = plan(st, cfg, agent=cfg.agent.id or log.agent_id)
    now = time.time()
    leases = st.active_leases(now, cfg.lease.grace_s)
    live_items = [i for i in st.items.values() if not i.removed]

    tasks: dict[str, int] = {}
    for i in live_items:
        if i.kind == "task":
            tasks[i.state] = tasks.get(i.state, 0) + 1
    bugs = _bugs(st)

    overview = {
        "workflow": {
            "model": cfg.flow.model,
            "integration": cfg.flow.integration,
            "max_parallel_tasks": cfg.schedule.max_parallel_tasks,
            "task_pipeline": list(cfg.gates.task_pipeline),
            "phase_pipeline": list(cfg.gates.phase_pipeline),
        },
        "workflow_diagram": _diagram(list(cfg.gates.task_pipeline)),
        "rules": _rules(repo),
        "decisions": _decisions(st),
        "active_work": {
            "active_leases": len(leases),
            "running": [i.id for i in live_items if i.state == "running"][:10],
        },
        "project": {
            "phases": len(st.phases()),
            "tasks": tasks,
            "bugs_open": len(bugs),
        },
        "task_queue": {
            "ready": [_task(i) for i in p.ready[:_READY_SHOWN]],
            "ready_total": len(p.ready),
            "in_progress": [_task(i) for i in p.running[:_READY_SHOWN]],
            "blocked": [
                {"id": b.item, "reason": b.reason, "waiting_on": b.waiting_on[:3]}
                for b in p.blocked[:_BLOCKED_SHOWN]
            ],
            "blocked_total": len(p.blocked),
        },
        "bugs": {"open": bugs[:_BUGS_SHOWN], "total_open": len(bugs)},
        "blockers": {"count": len(p.blocked), "capped": len(p.capped)},
        "discovery_hints": [
            "`ddflow brief` — what to do next and what governs it",
            "`ddflow board` — every item and its state",
            "`ddflow show <id>` — one item in full",
            "`ddflow bug list` / `ddflow decision list` / `ddflow workflow` — details",
        ],
    }
    return O.ok("workflow.state", **overview)


def _task(i) -> dict:
    return {"id": i.id, "title": i.title, "priority": i.priority, "phase": i.parent}


def _bugs(st) -> list[dict]:
    open_ = [b for b in st.bugs.values() if not b.fixed_at and not b.invalid_at]
    open_.sort(key=lambda b: (_SEVERITY.get(b.severity, 4), b.found_at))
    return [
        {
            "id": b.id,
            "title": b.title or b.summary[:80],
            "severity": b.severity or "unrated",
            "fix_task": b.fix_task,
        }
        for b in open_
    ]


def _decisions(st) -> dict:
    live = [d for d in st.decisions.values() if d.live]
    return {"active": len(live), "list": [{"id": d.id, "title": d.title} for d in live[:5]]}


def _rules(repo: Path) -> dict:
    from ..services.rules import RulesStorage

    rules = RulesStorage(repo).list()
    by_scope: dict[str, int] = {}
    for r in rules:
        by_scope[r.scope] = by_scope.get(r.scope, 0) + 1
    return {"total": len(rules), "by_scope": by_scope}


def _diagram(pipeline: list[str]) -> str:
    """Mermaid flowchart of the task pipeline: claim, each gate in order, merge, complete."""
    nodes = ["Claim"] + [re.sub(r"\W", "_", g) or "gate" for g in pipeline] + ["Complete"]
    labels = ["Claim"] + list(pipeline) + ["Complete"]
    lines = ["flowchart LR"]
    for a, b, lb in zip(nodes, nodes[1:], labels[1:], strict=False):
        lines.append(f"    {a} --> {b}[{lb}]")
    return "\n".join(lines)
