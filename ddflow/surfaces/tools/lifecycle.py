"""MCP tools: the loop: brief, next, claim, heartbeat, the gates, complete, merge.

One slice of the `TOOLS` registry, assembled in `surfaces/tools/__init__.py`."""

from __future__ import annotations

from typing import Any

from ..declared import lifecycle as L
from ._common import _api

TOOLS: dict[str, dict[str, Any]] = {
    "ddflow_brief": {
        "description": (
            "START HERE every session. Returns a budgeted pack: work recoverable after "
            "a crash, the current item, what is ready to start now, why everything else "
            "is blocked, and the past lessons ranked as relevant to this task. Use this "
            "INSTEAD of reading the project's lesson or rule files — it is the same "
            "information retrieved for the task at hand, at a fraction of the tokens."
        ),
        "properties": {
            "item": ("string", "Focus on this phase or task id (optional).", False),
            "phase": ("string", "Restrict the ready set to this phase (optional).", False),
            "check_recovery": (
                "boolean",
                "Also scan for crashed agents' worktrees and lead with them: unclaimed work left by a dead process is the one thing to know BEFORE picking up something new.",
                False,
            ),
        },
        "api": lambda repo, a, agent: _api().brief(
            repo,
            item=a.get("item", "") or "",
            phase=a.get("phase", "") or "",
            check_recovery=bool(a.get("check_recovery", _api().DEFAULT_CHECK_RECOVERY)),
            agent=agent,
        ),
        # PROSE: the budgeted reading pack is text to read.
        "payload": "text",
        "text": True,
        "kind": "brief",
    },
    "ddflow_next": L.BY_TOOL["ddflow_next"].tool_entry(),
    "ddflow_claim": L.BY_TOOL["ddflow_claim"].tool_entry(),
    "ddflow_heartbeat": L.BY_TOOL["ddflow_heartbeat"].tool_entry(),
    "ddflow_gate_status": L.BY_TOOL["ddflow_gate_status"].tool_entry(),
    "ddflow_gate_list": L.BY_TOOL["ddflow_gate_list"].tool_entry(),
    "ddflow_gate_run": L.BY_TOOL["ddflow_gate_run"].tool_entry(),
    "ddflow_gate_record": L.BY_TOOL["ddflow_gate_record"].tool_entry(),
    "ddflow_gate_verify": L.BY_TOOL["ddflow_gate_verify"].tool_entry(),
    "ddflow_gate_skip": L.BY_TOOL["ddflow_gate_skip"].tool_entry(),
    "ddflow_complete": L.BY_TOOL["ddflow_complete"].tool_entry(),
    "ddflow_merge": L.BY_TOOL["ddflow_merge"].tool_entry(),
}
