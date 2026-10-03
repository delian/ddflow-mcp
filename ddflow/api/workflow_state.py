"""Workflow state overview: configuration, decisions, active work, and queue."""

from __future__ import annotations

from pathlib import Path

from ..core import outcome as O
from ..services.waits import Waiter
from ._base import _load


def workflow_state(repo: Path, agent: str = "") -> O.Outcome:
    """Return comprehensive workflow state: config, rules, decisions, active work, queue.

    Args:
        repo: Path to the repository root
        agent: Agent ID for logging

    Returns:
        Outcome with workflow_overview (prose or structured data)
    """
    log, cfg, st = _load(repo, agent)

    # Gather components
    workflow_config = _gather_workflow_config(cfg)
    rules_summary = _gather_rules_summary(st)
    decisions = _gather_decisions(st)
    active_work = _gather_active_work(st)
    project_summary = _gather_project_summary(st)
    queue_state = _gather_queue_state(st, log)
    blockers = _gather_blockers(st)

    overview = {
        "workflow": workflow_config,
        "rules": rules_summary,
        "decisions": decisions,
        "active_work": active_work,
        "project": project_summary,
        "queue": queue_state,
        "blockers": blockers,
        "discovery_hints": [
            "Ask: 'show rules affecting task X'",
            "Ask: 'list decisions about Y'",
            "Ask: 'what's blocking phase Z'",
            "Command: `ddflow brief` for current queue",
            "Command: `ddflow workflow` to manage flows",
        ],
    }

    return O.ok("workflow.state", overview=overview)


def _gather_workflow_config(cfg) -> dict:
    """Extract workflow configuration."""
    flow_cfg = cfg.flow if hasattr(cfg, "flow") else None
    return {
        "type": getattr(flow_cfg, "integration", "unknown"),
        "branching": getattr(flow_cfg, "branching", "unknown"),
        "release_cadence": getattr(flow_cfg, "release_cadence", "unknown"),
        "max_parallel_tasks": getattr(
            cfg.schedule if hasattr(cfg, "schedule") else None, "max_parallel_tasks", 4
        ),
    }


def _gather_rules_summary(st) -> dict:
    """Summarize project rules by scope."""
    rules_by_scope = {}
    if hasattr(st, "rules"):
        for rule in st.rules.list():
            scope = rule.scope if hasattr(rule, "scope") else "unknown"
            if scope not in rules_by_scope:
                rules_by_scope[scope] = []
            rules_by_scope[scope].append({"id": rule.id, "title": getattr(rule, "title", "")})

    return {
        "total": sum(len(v) for v in rules_by_scope.values()),
        "by_scope": {k: len(v) for k, v in rules_by_scope.items()},
        "examples": {k: v[:2] for k, v in rules_by_scope.items()},
    }


def _gather_decisions(st) -> dict:
    """Gather active architecture decisions."""
    decisions_list = []
    if hasattr(st, "decisions"):
        for decision in st.decisions.values():
            if hasattr(decision, "status") and decision.status == "accepted":
                decisions_list.append(
                    {
                        "id": getattr(decision, "id", ""),
                        "title": getattr(decision, "title", ""),
                        "by": getattr(decision, "by", ""),
                    }
                )

    return {"active": len(decisions_list), "list": decisions_list[:5]}


def _gather_active_work(st) -> dict:
    """Summarize active work and leases."""
    active_leases = 0
    active_items = 0

    if hasattr(st, "leases"):
        active_leases = len([l for l in st.leases if l.item])

    if hasattr(st, "items"):
        active_items = len([i for i in st.items.values() if i.state == "in_progress"])

    return {
        "active_leases": active_leases,
        "items_in_progress": active_items,
        "agents_working": min(active_leases, active_items),
    }


def _gather_project_summary(st) -> dict:
    """Summarize project structure and state."""
    phases = {}
    tasks_by_state = {"open": 0, "in_progress": 0, "blocked": 0, "done": 0}
    bugs_open = 0

    if hasattr(st, "phases"):
        phases = {p.id: p.title for p in st.phases}

    if hasattr(st, "items"):
        for item in st.items.values():
            if hasattr(item, "kind") and item.kind == "bug":
                if hasattr(item, "state") and item.state == "open":
                    bugs_open += 1
            else:
                state = getattr(item, "state", "unknown")
                if state in tasks_by_state:
                    tasks_by_state[state] += 1

    return {
        "phases": len(phases),
        "tasks": tasks_by_state,
        "bugs_open": bugs_open,
        "phase_titles": list(phases.values())[:5],
    }


def _gather_queue_state(st, log) -> dict:
    """Summarize queue state and next work."""
    ready = []
    blocked = []

    if hasattr(st, "items"):
        for item in st.items.values():
            if hasattr(item, "state"):
                if item.state == "open" and not hasattr(item, "needs"):
                    ready.append({"id": item.id, "title": getattr(item, "title", "")})
                elif item.state == "open" and hasattr(item, "needs"):
                    blocked.append({"id": item.id, "blocking": item.needs})

    return {
        "ready_count": len(ready),
        "blocked_count": len(blocked),
        "next": ready[:3] if ready else [],
        "samples_blocked": blocked[:2] if blocked else [],
    }


def _gather_blockers(st) -> dict:
    """Identify blockers and conflicts."""
    blockers_list = []

    if hasattr(st, "waits"):
        for wait in st.waits:
            if isinstance(wait, Waiter):
                blockers_list.append(
                    {"item": getattr(wait, "item", ""), "reason": getattr(wait, "reason", "")}
                )

    return {
        "count": len(blockers_list),
        "list": blockers_list[:3],
    }
