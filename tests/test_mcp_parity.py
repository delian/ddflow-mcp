"""Every CLI command must be reachable over MCP.

The requirement: an operator in a chat window — possibly driving a remote agent — must
be able to check status, inspect history and run the workflow without a shell. Any CLI
command with no MCP equivalent is a capability that silently does not exist for them,
and the gap is invisible from either side: the CLI works, the tool list looks full.

This is a ratchet. The exemption list may only SHRINK, and each entry carries the
reason it is not a gap.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from orchard.cli import build_parser
from orchard.mcp_server import TOOLS

#: CLI command -> the MCP tool(s) that cover it, when the names differ.
ALIASES: dict[str, tuple[str, ...]] = {
    "init": ("orchard_setup",),
    "adopt": ("orchard_setup",),
    "config": ("orchard_configure",),
}

#: CLI commands deliberately NOT exposed, each with its reason.
NOT_EXPOSED: dict[str, str] = {
    "mcp": "starts the MCP server itself; exposing it over MCP would be recursive",
}


def cli_commands() -> list[str]:
    parser = build_parser()
    action = parser._subparsers._group_actions[0]
    return sorted(action.choices)


def covered(cmd: str) -> bool:
    if cmd in NOT_EXPOSED:
        return True
    for alias in ALIASES.get(cmd, ()):
        if alias in TOOLS:
            return True
    return any(t == f"orchard_{cmd}" or t.startswith(f"orchard_{cmd}_") for t in TOOLS)


def test_every_cli_command_is_reachable_over_mcp():
    missing = [c for c in cli_commands() if not covered(c)]
    assert not missing, (
        f"CLI commands with no MCP tool: {missing}. An operator in a chat window "
        f"cannot reach these at all. Add a tool, or add an entry to NOT_EXPOSED with "
        f"the reason."
    )


def test_the_exemption_list_only_shrinks():
    stale = [c for c in NOT_EXPOSED if c not in cli_commands()]
    assert not stale, f"exemptions for commands that no longer exist: {stale}"
    for cmd, reason in NOT_EXPOSED.items():
        assert len(reason) > 20, f"{cmd}: the exemption needs a real reason"


def test_the_aliases_all_resolve():
    for cmd, tools in ALIASES.items():
        assert cmd in cli_commands(), f"alias for a command that does not exist: {cmd}"
        for t in tools:
            assert t in TOOLS, f"{cmd} aliases {t}, which is not a tool"


def test_the_detector_can_fail():
    """Planted bad input: a command with no tool must be reported."""
    assert not covered("definitely_not_a_tool_xyzzy")


def test_the_status_and_recall_tools_exist_and_say_what_they_are_for():
    """These two are what an operator asks for in words — 'what is the status', 'have
    we done this before' — so their descriptions have to be recognisable as answers to
    those questions, not as API docs."""
    for name, must in (
        ("orchard_status", "status of this project"),
        ("orchard_recall", "HAVE WE BEEN HERE BEFORE"),
    ):
        assert name in TOOLS, name
        assert must.lower() in TOOLS[name]["description"].lower(), name


def test_every_tool_description_is_substantial():
    """The description is the ONLY thing a model sees when deciding whether to call a
    tool. A thin one is a tool that does not get used, or gets used wrongly."""
    thin = [n for n, spec in TOOLS.items() if len(spec["description"]) < 60]
    assert not thin, f"tool descriptions too thin to choose by: {thin}"
