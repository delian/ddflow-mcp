"""MCP tools: adding work to the queue: phases, splits, tasks.

One slice of the `TOOLS` registry, assembled in `surfaces/tools/__init__.py`."""

from __future__ import annotations

from typing import Any

from ..declared import queue as Q

TOOLS: dict[str, dict[str, Any]] = {
    "ddflow_phase_add": Q.BY_TOOL["ddflow_phase_add"].tool_entry(),
    "ddflow_split": Q.BY_TOOL["ddflow_split"].tool_entry(),
    "ddflow_task_add": Q.BY_TOOL["ddflow_task_add"].tool_entry(),
}
