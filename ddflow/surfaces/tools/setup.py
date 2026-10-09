"""MCP tools: setup, configuration, reviewers and reviews.

One slice of the `TOOLS` registry, assembled in `surfaces/tools/__init__.py`."""

from __future__ import annotations

from typing import Any

from ..declared import review as RV
from ..declared import setup as ST

TOOLS: dict[str, dict[str, Any]] = {
    "ddflow_setup": ST.BY_TOOL["ddflow_setup"].tool_entry(),
    "ddflow_configure": ST.BY_TOOL["ddflow_configure"].tool_entry(),
    "ddflow_reviewers_detect": RV.BY_TOOL["ddflow_reviewers_detect"].tool_entry(),
    "ddflow_reviewers_list": RV.BY_TOOL["ddflow_reviewers_list"].tool_entry(),
    "ddflow_review": RV.BY_TOOL["ddflow_review"].tool_entry(),
    "ddflow_review_triage": RV.BY_TOOL["ddflow_review_triage"].tool_entry(),
}
