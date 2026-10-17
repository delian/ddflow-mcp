"""The cadence and pins commands, declared once, with the tools they have.

`surfaces/parsers/maintenance.py` registers their command-line halves and
`surfaces/tools/__init__.py` takes their MCP entries (D-unify 4,
B-uni-cmd-migrate.6g-memory). Their handlers stay in `surfaces/commands/operations.py`.
"""

from __future__ import annotations

from ...api import pin_files as PF  # not re-exported by `api`: `_api().pins_regenerate` raised
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
        summary="which text of an instruction file a test pins; or `pins regenerate` re-records stale pin files",
        tool="ddflow_pins",
        description=(
            "BEFORE rewording an instruction file (rulebook, driver, AGENTS.md, CLAUDE.md, prompt template): which of its text a test pins, which suites to re-run, and the longest stretches no test holds. Exit 2: no Python suite to read pins from -- treat ALL as pinned. `pins regenerate [--check] [--only NAME] [--install]` re-records stale pin files by [pins].recipes_file."
        ),
        wants_progress=True,
        call=lambda repo, a, agent, on_progress=None: (
            PF.pins_regenerate(
                repo,
                on_progress=on_progress,
                only=a.get("only") or "",
                check=bool(a.get("check")),
                install=bool(a.get("install")),
                after_merge=bool(a.get("after_merge")),
                verb=a["document"],
                target=a.get("target") or "",
                agent=agent,
            )
            if a["document"] in ("regenerate", "merge-driver")
            else _api().pins(
                repo,
                a["document"],
                tests=tuple(t.strip() for t in (a.get("tests") or "").split(",") if t.strip()),
                min_chars=None if a.get("min_needle") is None else int(a["min_needle"]),
                top=int(a.get("top") if a.get("top") is not None else 10),
            )
        ),
        payload="",
        params=(
            Param(
                "document",
                help="Instruction file path; or `regenerate`.",
                cli_help="the instruction file, e.g. AGENTS.md; or `regenerate` to re-record stale pin files",
                positional=True,
            ),
            Param(
                "target",
                positional=True,
                nargs="*",
                default="",
                cli_only=True,
                cli_help="merge-driver (git runs it): the files holding our side, the common ancestor and the other side",
            ),
            Param(
                "check",
                type="boolean",
                help="regenerate: list stale pins only.",
                cli_help="regenerate: list the stale pins and write nothing (exit 1 when any)",
            ),
            Param(
                "only",
                help="regenerate: just this pin.",
                default="",
                cli_help="regenerate: just the pin of this name",
            ),
            Param(
                "install",
                type="boolean",
                help="regenerate: install merge driver + hook.",
                cli_help="regenerate: install the git merge driver and post-merge hook for the pin files",
            ),
            Param(
                "after_merge",
                type="boolean",
                help="regenerate: the post-merge hook entry.",
                cli_help="regenerate: what the post-merge hook runs",
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
