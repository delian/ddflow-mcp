"""MCP tools: the gate pipeline and its checks: workflow, verify, ci, help.

One slice of the `TOOLS` registry, assembled in `surfaces/tools/__init__.py`."""

from __future__ import annotations

from typing import Any

from ..declared import export as E
from ..declared import hooks as H
from ..declared import review as RV
from ..declared import setup as ST

TOOLS: dict[str, dict[str, Any]] = {
    "ddflow_workflow": H.BY_TOOL["ddflow_workflow"].tool_entry(),
    "ddflow_workflow_pipeline": H.BY_TOOL["ddflow_workflow_pipeline"].tool_entry(),
    "ddflow_workflow_gate": H.BY_TOOL["ddflow_workflow_gate"].tool_entry(),
    "ddflow_workflow_drop": H.BY_TOOL["ddflow_workflow_drop"].tool_entry(),
    "ddflow_workflow_state": H.BY_TOOL["ddflow_workflow_state"].tool_entry(),
    "ddflow_verify": RV.BY_TOOL["ddflow_verify"].tool_entry(),
    "ddflow_ci": E.BY_TOOL["ddflow_ci"].tool_entry(),
    "ddflow_help": H.BY_TOOL["ddflow_help"].tool_entry(),
    "ddflow_import_verify": ST.BY_TOOL["ddflow_import_verify"].tool_entry(),
}
