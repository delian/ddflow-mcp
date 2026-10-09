"""MCP tools: long-running jobs, memory, resolve/unblock, session start.

One slice of the `TOOLS` registry, assembled in `surfaces/tools/__init__.py`."""

from __future__ import annotations

from typing import Any

from ..declared import lifecycle as L
from ..declared import memory as M
from ..declared import queue as Q
from ..declared import records as R

TOOLS: dict[str, dict[str, Any]] = {
    "ddflow_job_run": M.BY_TOOL["ddflow_job_run"].tool_entry(),
    "ddflow_job_add": M.BY_TOOL["ddflow_job_add"].tool_entry(),
    "ddflow_job_list": M.BY_TOOL["ddflow_job_list"].tool_entry(),
    "ddflow_job_end": M.BY_TOOL["ddflow_job_end"].tool_entry(),
    "ddflow_memory_add": M.BY_TOOL["ddflow_memory_add"].tool_entry(),
    "ddflow_memory_list": M.BY_TOOL["ddflow_memory_list"].tool_entry(),
    "ddflow_memory_forget": M.BY_TOOL["ddflow_memory_forget"].tool_entry(),
    "ddflow_resolve": Q.BY_TOOL["ddflow_resolve"].tool_entry(),
    "ddflow_unblock": L.BY_TOOL["ddflow_unblock"].tool_entry(),
    "ddflow_session_start": R.BY_TOOL["ddflow_session_start"].tool_entry(),
}
