"""MCP tools: project rules.

One slice of the `TOOLS` registry, assembled in `surfaces/tools/__init__.py`."""

from __future__ import annotations

from typing import Any

from ..declared import rules as RU

TOOLS: dict[str, dict[str, Any]] = {
    "ddflow_rule_add": RU.BY_TOOL["ddflow_rule_add"].tool_entry(),
    "ddflow_rule_list": RU.BY_TOOL["ddflow_rule_list"].tool_entry(),
    "ddflow_rule_search": RU.BY_TOOL["ddflow_rule_search"].tool_entry(),
    "ddflow_rule_edit": RU.BY_TOOL["ddflow_rule_edit"].tool_entry(),
    "ddflow_rule_remove": RU.BY_TOOL["ddflow_rule_remove"].tool_entry(),
    "ddflow_rule_sync": RU.BY_TOOL["ddflow_rule_sync"].tool_entry(),
    "ddflow_rule_show": RU.BY_TOOL["ddflow_rule_show"].tool_entry(),
}
