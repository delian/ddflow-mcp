"""MCP tools: recording as you go: bugs found, session notes, decision show.

One slice of the `TOOLS` registry, assembled in `surfaces/tools/__init__.py`."""

from __future__ import annotations

from typing import Any

from ..declared import knowledge as K
from ..declared import records as R

TOOLS: dict[str, dict[str, Any]] = {
    "ddflow_bug_found": R.BY_TOOL["ddflow_bug_found"].tool_entry(),
    "ddflow_bug_file_tasks": R.BY_TOOL["ddflow_bug_file_tasks"].tool_entry(),
    "ddflow_session_note": R.BY_TOOL["ddflow_session_note"].tool_entry(),
    "ddflow_session_end": R.BY_TOOL["ddflow_session_end"].tool_entry(),
    "ddflow_decision_show": K.BY_TOOL["ddflow_decision_show"].tool_entry(),
}
