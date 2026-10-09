"""MCP tools: one item: show, update, abandon, remove, release, wait, block.

One slice of the `TOOLS` registry, assembled in `surfaces/tools/__init__.py`."""

from __future__ import annotations

from typing import Any

from ._common import (
    _api,
)

TOOLS: dict[str, dict[str, Any]] = {
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
