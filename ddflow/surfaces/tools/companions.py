"""MCP tools: companion tools and polluter bisection.

One slice of the `TOOLS` registry, assembled in `surfaces/tools/__init__.py`."""

from __future__ import annotations

from typing import Any

from ..declared import export as E
from ..declared import hooks as H

TOOLS: dict[str, dict[str, Any]] = {
    "ddflow_companions": H.BY_TOOL["ddflow_companions"].tool_entry(),
    "ddflow_bisect": E.BY_TOOL["ddflow_bisect"].tool_entry(),
    "ddflow_companions_verify": H.BY_TOOL["ddflow_companions_verify"].tool_entry(),
    "ddflow_companions_add": H.BY_TOOL["ddflow_companions_add"].tool_entry(),
}
