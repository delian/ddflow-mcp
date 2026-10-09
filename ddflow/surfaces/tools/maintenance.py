"""MCP tools: prompts, hooks, doctor, cadence, export, pins, precommit, tests.

One slice of the `TOOLS` registry, assembled in `surfaces/tools/__init__.py`."""

from __future__ import annotations

from typing import Any

from ..declared import cadence as C
from ..declared import export as E
from ..declared import hooks as H
from ..declared import records as R
from ..declared import setup as ST

TOOLS: dict[str, dict[str, Any]] = {
    "ddflow_prompts": H.BY_TOOL["ddflow_prompts"].tool_entry(),
    "ddflow_hooks": H.BY_TOOL["ddflow_hooks"].tool_entry(),
    "ddflow_upgrade": ST.BY_TOOL["ddflow_upgrade"].tool_entry(),
    "ddflow_doctor": ST.BY_TOOL["ddflow_doctor"].tool_entry(),
    "ddflow_cadence": C.BY_TOOL["ddflow_cadence"].tool_entry(),
    "ddflow_export": E.BY_TOOL["ddflow_export"].tool_entry(),
    "ddflow_pins": C.BY_TOOL["ddflow_pins"].tool_entry(),
    "ddflow_precommit": E.BY_TOOL["ddflow_precommit"].tool_entry(),
    "ddflow_tests": E.BY_TOOL["ddflow_tests"].tool_entry(),
    "ddflow_session_prompt": R.BY_TOOL["ddflow_session_prompt"].tool_entry(),
}
