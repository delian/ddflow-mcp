"""The two surfaces differ only where a difference is declared (B-cli-mcp-parity-identify-list,
Bf9b9abac5b).

The forward ratchet (`test_mcp_parity.py`) holds every CLI command to a tool. These hold the
other direction: an MCP tool with no CLI twin must be declared, with its reason, and the
`ddflow_list` kinds MCP has (`session`, `search`) must be words the shell's `list` knows.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _cli_helpers import run_cli
from test_mcp_parity import cli_commands, cli_leaves

from ddflow.surfaces import exemptions as X
from ddflow.surfaces.mcp import TOOLS
from ddflow.surfaces.registry import CLI_FLAG_COMMANDS


def _has_cli_twin(tool: str) -> bool:
    stem = tool.removeprefix("ddflow_")
    for path in cli_leaves():
        joined = "_".join(path).replace("-", "_")
        if joined == stem or joined.startswith(stem + "_") or stem.startswith(joined + "_"):
            return True
    if any(c.via and c.via[0] == tool for c in X.DECLARATIONS if c.path):
        return True
    if stem in CLI_FLAG_COMMANDS.values():
        return True  # a flag of a command: `companions --verify`
    return any(tool in tools for tools in X.COVERING_TOOLS.values())


def test_every_tool_without_a_cli_twin_declares_why():
    declared = {c.tool for c in X.DECLARATIONS if c.tool and not c.path and c.reason}
    bare = [t for t in sorted(TOOLS) if not _has_cli_twin(t) and t not in declared]
    assert not bare, (
        f"MCP tools with no CLI command and no declared reason: {bare}. Add the command, or "
        "a `Command(path=(), tool=..., reason=...)` in surfaces/exemptions.py."
    )


def test_the_reverse_detector_can_fail():
    assert not _has_cli_twin("ddflow_definitely_not_a_tool")
    assert cli_commands()


def test_list_session_is_the_session_list(repo):
    rc, out, err = run_cli(repo, "list", "session")
    rc2, out2, _ = run_cli(repo, "session", "list")
    assert "invalid choice" not in err
    assert (rc, out) == (rc2, out2)


def test_list_search_is_the_search_verb(repo):
    rc, _, err = run_cli(repo, "list", "search")
    assert rc == 3 and "--query" in err
    rc, out, _ = run_cli(repo, "list", "search", "--query", "zzzznomatch")
    rc2, out2, _ = run_cli(repo, "search", "zzzznomatch")
    assert (rc, out) == (rc2, out2)
    assert run_cli(repo, "list", "task", "--query", "x")[0] == 3
    assert run_cli(repo, "list", "session", "--query", "x")[0] == 3


def test_identify_is_a_shell_verb(repo):
    rc, out, err = run_cli(repo, "identify", "--help")
    assert rc == 0 and "invalid choice" not in err
    rc, out, err = run_cli(repo, "identify", agent="w1")
    assert rc == 0 and "'w1'" in out
