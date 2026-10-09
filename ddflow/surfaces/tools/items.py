"""MCP tools: one item: show, update, abandon, remove, release, wait, block.

One slice of the `TOOLS` registry, assembled in `surfaces/tools/__init__.py`."""

from __future__ import annotations

from typing import Any

from ..declared import lifecycle as L
from ..declared import queue as Q
from ..declared import reporting as R
from ._common import (
    _api,
)

TOOLS: dict[str, dict[str, Any]] = {
    "ddflow_show": R.BY_TOOL["ddflow_show"].tool_entry(),
    "ddflow_update": Q.BY_TOOL["ddflow_update"].tool_entry(),
    "ddflow_abandon": L.BY_TOOL["ddflow_abandon"].tool_entry(),
    "ddflow_remove": L.BY_TOOL["ddflow_remove"].tool_entry(),
    "ddflow_release": L.BY_TOOL["ddflow_release"].tool_entry(),
    "ddflow_wait": L.BY_TOOL["ddflow_wait"].tool_entry(),
    "ddflow_block": L.BY_TOOL["ddflow_block"].tool_entry(),
    "ddflow_external_sync": {
        "description": (
            "Observe the items in SIBLING repositories that this queue depends on "
            "(`needs = ['run_nemo_run:132.D']`, repositories named in [schedule] repos), "
            "and record what changed in this log. An external dependency is met only "
            "once it has been observed done here, so run this before `ddflow_next` when "
            "work waits on another project. Reads the other repository; never writes it."
        ),
        "properties": {},
        "api": lambda repo, a, agent: _api().external_sync(repo, agent=agent),
        "payload": "observed",
    },
}
