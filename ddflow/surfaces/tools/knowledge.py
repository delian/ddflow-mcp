"""MCP tools: lessons, research, and closing bugs.

One slice of the `TOOLS` registry, assembled in `surfaces/tools/__init__.py`."""

from __future__ import annotations

from typing import Any

from ..declared import knowledge as K
from ..declared import records as R

TOOLS: dict[str, dict[str, Any]] = {
    "ddflow_lesson_verify": K.BY_TOOL["ddflow_lesson_verify"].tool_entry(),
    "ddflow_lesson_add": K.BY_TOOL["ddflow_lesson_add"].tool_entry(),
    "ddflow_lesson_search": K.BY_TOOL["ddflow_lesson_search"].tool_entry(),
    "ddflow_research_add": R.BY_TOOL["ddflow_research_add"].tool_entry(),
    "ddflow_bug_fixed": R.BY_TOOL["ddflow_bug_fixed"].tool_entry(),
    "ddflow_bug_invalid": R.BY_TOOL["ddflow_bug_invalid"].tool_entry(),
}
