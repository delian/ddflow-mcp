"""MCP tools: reading the queue and finding things: recover, board, recall, status, identity.

One slice of the `TOOLS` registry, assembled in `surfaces/tools/__init__.py`."""

from __future__ import annotations

from typing import Any

from ..declared import knowledge as K
from ..declared import reporting as R
from ._common import _api

TOOLS: dict[str, dict[str, Any]] = {
    "ddflow_recover": R.BY_TOOL["ddflow_recover"].tool_entry(),
    "ddflow_board": R.BY_TOOL["ddflow_board"].tool_entry(),
    "ddflow_progress": R.BY_TOOL["ddflow_progress"].tool_entry(),
    "ddflow_loops": R.BY_TOOL["ddflow_loops"].tool_entry(),
    "ddflow_cleanup": R.BY_TOOL["ddflow_cleanup"].tool_entry(),
    "ddflow_onboard": {
        "description": (
            "The onboarding stages in one call: status (standing drift report), preflight, legacy, memory, test-gate, verify. Propose by default; apply=true acts on the safe/approved items and accepts names."
        ),
        "properties": {
            "stage": (
                "string",
                "A stage name: status (the drift report, default), preflight, legacy, memory, test-gate or verify.",
                False,
            ),
            "apply": ("boolean", "Act on the proposal (preflight/legacy/memory).", False),
            "accept": (
                "array",
                "Names to approve (preflight leftovers, memory facts); empty = everything the report marked safe.",
                False,
            ),
        },
        "api": lambda repo, a, agent: _api().onboard_run(
            repo,
            stage=str(a.get("stage") or "status"),
            apply=bool(a.get("apply")),
            accept=tuple(a.get("accept") or ()),
            agent=agent,
        ),
        "payload": "",
    },
    "ddflow_recall": K.BY_TOOL["ddflow_recall"].tool_entry(),
    "ddflow_similar": K.BY_TOOL["ddflow_similar"].tool_entry(),
    "ddflow_dupes": K.BY_TOOL["ddflow_dupes"].tool_entry(),
    "ddflow_link": K.BY_TOOL["ddflow_link"].tool_entry(),
    "ddflow_status": R.BY_TOOL["ddflow_status"].tool_entry(),
    "ddflow_identify": {
        "description": (
            "Declare WHO you are on this connection before anything that writes. Call it first when 2+ agents or subagents work this repository at once: identity attributes every claim, gate outcome and review, and the tree-derived default merges several agents in one tree into one identity with no error (a review would pass independence against itself). Pick a short stable name (your role), distinct from the others'. Idempotent. A SUBAGENT sharing its parent's connection must NOT call this; it passes `as_agent` on each call instead (the CLI's `--agent`). Stateless 2026-07-28 requests: persists nothing; name yourself per call (as_agent or _meta ddflow/agent)."
        ),
        "properties": {
            "agent": (
                "string",
                "A short stable name, e.g. 'reviewer-2' (letters, digits, . _ -; max 64; it names your log file). OMIT to reset to the tree-derived default.",
                False,
            ),
        },
        "identify": True,
    },
}
