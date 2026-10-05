"""MCP tools: companion tools and polluter bisection.

One slice of the `TOOLS` registry, assembled in `surfaces/tools/__init__.py`."""

from __future__ import annotations

from typing import Any

from ._common import _api, _bisect

TOOLS: dict[str, dict[str, Any]] = {
    "ddflow_companions": {
        "description": (
            "Which companion MCP servers serve this project's gates, which are installed, which an agent launches. Without them, agent gates pass on assertion. Exit 2: a default one is missing or unregistered. Read-only; never installs or launches."
        ),
        "properties": {
            "no_probe": ("boolean", "Skip the detection probes (faster, less certain).", False),
        },
        "api": lambda repo, a, agent: _api().companions_list(
            repo, no_probe=bool(a.get("no_probe")), agent=agent
        ),
        "payload": ("companions", "gate_coverage", "uncovered_gates"),
    },
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
    "ddflow_companions_verify": {
        "description": (
            "Launch MCP companions and require a JSON-RPC answer to `initialize`. SPAWNS processes (opt-in). Default: registered or installed ones. Exit 1: not an MCP server; 2: unsure."
        ),
        "properties": {
            "id": ("string", "Comma-separated ids, installed or not.", False),
        },
        "api": lambda repo, a, agent: _api().companions_verify(
            repo, a.get("id", "") or "", agent=agent
        ),
        "payload": ("verified", "skipped"),
    },
    "ddflow_companions_add": {
        "description": (
            "WRITES the agent config: registers companion MCP servers that are ALREADY installed (exit 3 for one that is not). dry_run=true FIRST; show the operator the entry: which servers an agent launches is their decision."
        ),
        "properties": {
            "id": ("string", "Comma-separated ids; default: every installed one.", False),
            "agents": ("string", "Comma-separated agent keys (default: claude).", False),
            "dry_run": (
                "boolean",
                "Report the exact entry that would be written; write nothing.",
                False,
            ),
        },
        "api": lambda repo, a, agent: _api().companions_add(
            repo,
            _api().Registration(
                ids=a.get("id", "") or "",
                agents=a.get("agents", "") or "",
                force=bool(a.get("force")),
                dry_run=bool(a.get("dry_run")),
            ),
            agent=agent,
        ),
        "payload": ("actions", "applied", "written", "refused"),
    },
}
