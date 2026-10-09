"""MCP tools: decisions: add, list, applicable, supersede.

One slice of the `TOOLS` registry, assembled in `surfaces/tools/__init__.py`."""

from __future__ import annotations

from typing import Any

from ..declared import knowledge as K

TOOLS: dict[str, dict[str, Any]] = {
    "ddflow_decision_add": K.BY_TOOL["ddflow_decision_add"].tool_entry(),
    "ddflow_decision_list": K.BY_TOOL["ddflow_decision_list"].tool_entry(),
    "ddflow_decision_applicable": K.BY_TOOL["ddflow_decision_applicable"].tool_entry(),
    "ddflow_decision_supersede": K.BY_TOOL["ddflow_decision_supersede"].tool_entry(),
}
