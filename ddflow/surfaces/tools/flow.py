"""MCP tools: pull requests, versions, promotion and the branching flow.

One slice of the `TOOLS` registry, assembled in `surfaces/tools/__init__.py`. The entries are
generated from the declarations in `surfaces/declared/flow.py`."""

from __future__ import annotations

from typing import Any

from ..declared import flow as F

TOOLS: dict[str, dict[str, Any]] = {
    "ddflow_pr_sync": F.BY_TOOL["ddflow_pr_sync"].tool_entry(),
    "ddflow_pr_status": F.BY_TOOL["ddflow_pr_status"].tool_entry(),
    "ddflow_pr_threads": F.BY_TOOL["ddflow_pr_threads"].tool_entry(),
    "ddflow_version_show": F.BY_TOOL["ddflow_version_show"].tool_entry(),
    "ddflow_version_cut": F.BY_TOOL["ddflow_version_cut"].tool_entry(),
    "ddflow_promote_add": F.BY_TOOL["ddflow_promote_add"].tool_entry(),
    "ddflow_promote_deployed": F.BY_TOOL["ddflow_promote_deployed"].tool_entry(),
    "ddflow_promote_status": F.BY_TOOL["ddflow_promote_status"].tool_entry(),
    "ddflow_flow_show": F.BY_TOOL["ddflow_flow_show"].tool_entry(),
    "ddflow_flow_choose": F.BY_TOOL["ddflow_flow_choose"].tool_entry(),
}
