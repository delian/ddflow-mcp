"""The cadence and pins commands, declared once, with the tools they have.

`surfaces/parsers/maintenance.py` registers their command-line halves and
`surfaces/tools/maintenance.py` takes their MCP entries (D-unify 4,
B-uni-cmd-migrate.6g-memory). Their handlers stay in `surfaces/commands/operations.py`.
"""

from __future__ import annotations

from ..registry import Command, Param, by_tool
from ..tools._common import _api

COMMANDS: tuple[Command, ...] = (
    Command(
        path=("cadence",),
        summary="which periodic passes are due (exit 2 = none)",
        tool="ddflow_cadence",
        description=(
            "Which periodic whole-repo passes are due — integration tests, "
            "architecture review, mutation testing, dedupe sweep, lessons "
            "compression. Derived from completed work, so there is no state "
            "file to drift."
        ),
        call=lambda repo, a, agent: _api().cadence(
            repo, ran=a.get("ran", "") or "", note=a.get("note", "") or "", agent=agent
        ),
        payload="due",
        params=(
            Param("ran", help="Record that this cadence just ran.", cli_help="", default=""),
            Param(
                "note",
                help="What the pass did, recorded with it.",
                cli_help="",
                default="",
            ),
        ),
    ),
    Command(
        path=("pins",),
        summary="which text of an instruction file a test pins, before you compress it",
        tool="ddflow_pins",
        description=(
            "BEFORE compressing or rewording an instruction file (a rulebook, a driver, AGENTS.md, CLAUDE.md, a prompt template): which of its text a test pins, which suites to re-run afterwards, and the longest stretches no test holds. A sentence that reads like rationale is often a rule a test asserts. Exit 2: no Python suite to read pins from -- then treat ALL of it as pinned. Free text is a lower bound, not permission."
        ),
        call=lambda repo, a, agent: _api().pins(
            repo,
            a["document"],
            tests=tuple(t.strip() for t in (a.get("tests") or "").split(",") if t.strip()),
            min_chars=None if a.get("min_needle") is None else int(a["min_needle"]),
            top=int(a.get("top") if a.get("top") is not None else 10),
        ),
        payload="",
        params=(
            Param(
                "document",
                help="Path of the instruction file, relative to the repo.",
                cli_help="the instruction file, e.g. AGENTS.md",
                positional=True,
            ),
            Param(
                "tests",
                help="Comma-separated test dirs (default: tests,test).",
                cli_help="comma-separated test dirs (default: tests,test)",
                default="",
            ),
            Param(
                "min_needle",
                type="integer",
                help="Shortest string literal that counts as a pin (default 12).",
                cli_help="shortest literal that counts as a pin (default 12)",
                default=None,
            ),
            Param(
                "top",
                type="integer",
                help="How many free stretches to return (default 10).",
                cli_help="how many free stretches to show",
                default=10,
            ),
        ),
    ),
)

BY_TOOL = by_tool(COMMANDS)
