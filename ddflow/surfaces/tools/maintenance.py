"""MCP tools: prompts, hooks, doctor, cadence, export, pins, precommit, tests.

One slice of the `TOOLS` registry, assembled in `surfaces/tools/__init__.py`."""

from __future__ import annotations

from typing import Any

from ..declared import export as E
from ..declared import hooks as H
from ..declared import records as R
from ..declared import setup as ST
from ._common import _api

TOOLS: dict[str, dict[str, Any]] = {
    "ddflow_prompts": H.BY_TOOL["ddflow_prompts"].tool_entry(),
    "ddflow_hooks": H.BY_TOOL["ddflow_hooks"].tool_entry(),
    "ddflow_upgrade": ST.BY_TOOL["ddflow_upgrade"].tool_entry(),
    "ddflow_doctor": ST.BY_TOOL["ddflow_doctor"].tool_entry(),
    "ddflow_cadence": {
        "description": (
            "Which periodic whole-repo passes are due — integration tests, "
            "architecture review, mutation testing, dedupe sweep, lessons "
            "compression. Derived from completed work, so there is no state "
            "file to drift."
        ),
        "properties": {
            "ran": ("string", "Record that this cadence just ran.", False),
            "note": ("string", "What the pass did, recorded with it.", False),
        },
        "api": lambda repo, a, agent: _api().cadence(
            repo, ran=a.get("ran", "") or "", note=a.get("note", "") or "", agent=agent
        ),
        "payload": "due",
    },
    "ddflow_export": E.BY_TOOL["ddflow_export"].tool_entry(),
    "ddflow_pins": {
        "description": (
            "BEFORE compressing or rewording an instruction file (a rulebook, a driver, AGENTS.md, CLAUDE.md, a prompt template): which of its text a test pins, which suites to re-run afterwards, and the longest stretches no test holds. A sentence that reads like rationale is often a rule a test asserts. Exit 2: no Python suite to read pins from -- then treat ALL of it as pinned. Free text is a lower bound, not permission."
        ),
        "properties": {
            "document": ("string", "Path of the instruction file, relative to the repo.", True),
            "tests": ("string", "Comma-separated test dirs (default: tests,test).", False),
            "min_needle": (
                "integer",
                "Shortest string literal that counts as a pin (default 12).",
                False,
            ),
            "top": ("integer", "How many free stretches to return (default 10).", False),
        },
        "api": lambda repo, a, agent: _api().pins(
            repo,
            a["document"],
            tests=tuple(t.strip() for t in (a.get("tests") or "").split(",") if t.strip()),
            min_chars=None if a.get("min_needle") is None else int(a["min_needle"]),
            top=int(a.get("top") if a.get("top") is not None else 10),
        ),
        "payload": "",
    },
    "ddflow_precommit": E.BY_TOOL["ddflow_precommit"].tool_entry(),
    "ddflow_tests": E.BY_TOOL["ddflow_tests"].tool_entry(),
    "ddflow_session_prompt": R.BY_TOOL["ddflow_session_prompt"].tool_entry(),
}
