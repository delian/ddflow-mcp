"""MCP tools: brief (hand-written; the loop's tools are declared in `declared/lifecycle.py`).

Hand-written entries merged into `TOOLS` in `surfaces/tools/__init__.py`."""

from __future__ import annotations

from typing import Any

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
}
