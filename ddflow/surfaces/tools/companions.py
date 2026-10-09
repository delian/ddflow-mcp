"""MCP tools: companion tools and polluter bisection.

One slice of the `TOOLS` registry, assembled in `surfaces/tools/__init__.py`."""

from __future__ import annotations

from typing import Any

from ..declared import hooks as H
from ._common import _bisect

TOOLS: dict[str, dict[str, Any]] = {
    "ddflow_companions": H.BY_TOOL["ddflow_companions"].tool_entry(),
    "ddflow_bisect": {
        "description": (
            "Which earlier test file makes `victim` fail only in full-suite order? Delta-debugs the files before it, running `cmd` many times. Exit 2: nothing to report."
        ),
        "properties": {
            "victim": ("string", "Failing test id.", True),
            "cmd": ("string", "Test command; {tests} = the list.", True),
            "candidates": ("string", "Comma-separated files in run order.", False),
            "timeout": ("integer", "Seconds per run (600).", False),
        },
        "api": lambda repo, a, agent: _bisect(repo, a),
        "payload": ("state", "victim", "polluters", "candidates", "summary", "runs"),
    },
    "ddflow_companions_verify": H.BY_TOOL["ddflow_companions_verify"].tool_entry(),
    "ddflow_companions_add": H.BY_TOOL["ddflow_companions_add"].tool_entry(),
}
