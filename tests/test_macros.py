"""B83: `[[macro]]` — operator-defined prompt macros.

"Enter debugger mode." The machinery for a named, parameterised prompt with
project-override precedence already existed; what did not was any way for an OPERATOR to
add one. `COMMANDS` is a Python dict, so a mode specific to one project needed a code
change in ddflow. That was the whole gap.

What is deliberately absent is `dx-zero/mcpn`'s `toolMode: situational` — the model freely
picking from a bound set with no recorded ordering. `tools` here is declarative and
rendered into the prompt; it is not a permission boundary, MCP has no mechanism to make it
one, and `test_the_tool_list_is_declarative_not_a_permission_boundary` says so where
somebody would otherwise assume it.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pytest
from conftest import run_cli

from ddflow.services import macros as M
from ddflow.services import prompts as P

DEBUGGER = '''
[[macro]]
name = "debugger"
title = "Enter debugger mode"
description = "Reproduce first, then bisect. No fix without a failing probe."
params = ["symptom"]
tools = ["ddflow_bug_found", "ddflow_gate_run", "ddflow_bug_fixed"]
prompt = """
You are debugging: {{ symptom }}

Reproduce it before you theorise.
"""
'''


def _with(repo: Path, block: str) -> Path:
    run_cli(repo, "init")
    cfg = repo / ".ddflow" / "config.toml"
    cfg.write_text(cfg.read_text() + block)
    return cfg


# -- the gap this closes -------------------------------------------------------------------


def test_an_operator_can_add_a_mode_without_changing_ddflow(repo):
    """The entire point. No code change, no new file in the package."""
    _with(repo, DEBUGGER)
    macros = M.load_macros(repo)
    assert list(macros) == ["debugger"]
    assert macros["debugger"].title == "Enter debugger mode"
    assert macros["debugger"].params == ["symptom"]


def test_a_macro_is_listed_and_rendered_over_MCP(repo):
    """Beside the shipped workflows, not behind a separate call — a mode that has to be
    asked for by name is a mode nobody finds."""
    from ddflow.surfaces.mcp import Server

    _with(repo, DEBUGGER)
    srv = Server(repo)

    listed = srv.handle({"jsonrpc": "2.0", "id": 1, "method": "prompts/list"})["result"]["prompts"]
    entry = next((p for p in listed if p["name"] == "debugger"), None)
    assert entry is not None, [p["name"] for p in listed]
    assert entry["title"] == "Enter debugger mode"
    assert [a["name"] for a in entry["arguments"]] == ["symptom"]
    # And the shipped ones are still there.
    assert "bug-hunt" in [p["name"] for p in listed]

    got = srv.handle(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "prompts/get",
            "params": {"name": "debugger", "arguments": {"symptom": "the fold drops seq"}},
        }
    )
    text = got["result"]["messages"][0]["content"]["text"]
    assert "the fold drops seq" in text, text
    assert got["result"]["description"].startswith("Reproduce first")


def test_a_macro_is_visible_from_a_terminal_too(repo):
    """`ddflow prompts list` and `show`. Half the prompt library being reachable from an
    agent and invisible from a terminal is a bug this project has already had once."""
    _with(repo, DEBUGGER)
    code, out, err = run_cli(repo, "prompts", "list")
    assert code == 0, err
    assert "debugger" in out, out

    code, out, err = run_cli(repo, "prompts", "show", "debugger")
    assert code == 0, err
    assert "You are debugging" in out, out


# -- the three refusals, each because the alternative fails quietly ------------------------


def test_a_missing_parameter_is_refused_not_rendered_as_a_hole(repo):
    """A prompt with a hole in it reads as a complete instruction."""
    _with(repo, DEBUGGER)
    macro = M.load_macros(repo)["debugger"]
    with pytest.raises(M.MacroError, match="needs symptom"):
        M.render(macro, repo, {})
    with pytest.raises(M.MacroError, match="needs symptom"):
        M.render(macro, repo, {"symptom": "   "})
    assert "a real symptom" in M.render(macro, repo, {"symptom": "a real symptom"})


def test_a_macro_may_not_take_a_shipped_commands_name(repo):
    """Silent shadowing leaves the operator editing a block that does nothing, with every
    surface reporting the shipped description back at them."""
    _with(repo, '\n[[macro]]\nname = "bug-hunt"\nprompt = "mine"\n')
    with pytest.raises(M.MacroError, match="shipped workflow command"):
        M.load_macros(repo)


def test_prompt_and_prompt_file_are_mutually_exclusive(repo):
    """Two sources for one body means one of them is dead and looks live."""
    (repo / "mode.md").write_text("from a file")
    _with(
        repo,
        '\n[[macro]]\nname = "two-bodies"\nprompt = "inline"\nprompt_file = "mode.md"\n',
    )
    with pytest.raises(M.MacroError, match="both"):
        M.load_macros(repo)["two-bodies"].body(repo)


def test_a_macro_with_no_body_at_all_is_refused(repo):
    """A named mode with no instruction in it is a slash command that does nothing."""
    _with(repo, '\n[[macro]]\nname = "empty"\ntitle = "Looks real"\n')
    with pytest.raises(M.MacroError, match="no `prompt`"):
        M.load_macros(repo)["empty"].body(repo)


def test_a_missing_prompt_file_is_an_error_not_an_empty_body(repo):
    _with(repo, '\n[[macro]]\nname = "gone"\nprompt_file = "docs/not-here.md"\n')
    with pytest.raises(M.MacroError, match="does not exist"):
        M.load_macros(repo)["gone"].body(repo)


def test_an_unknown_key_in_a_macro_block_is_refused(repo):
    """`overlay_array` validates against the dataclass, so a typo'd knob is named rather
    than ignored — the silent-knob-drop class."""
    _with(repo, '\n[[macro]]\nname = "typo"\nprompt = "x"\ntoolz = ["a"]\n')
    with pytest.raises(ValueError, match="toolz"):
        M.load_macros(repo)


# -- what is NOT copied from mcpn ----------------------------------------------------------


def test_the_tool_list_is_declarative_not_a_permission_boundary(repo):
    """`tools` is rendered INTO the prompt and enforces nothing.

    `dx-zero/mcpn`'s `toolMode: situational` lets the model pick freely from a bound set
    with no recorded ordering or rationale, which reintroduces the non-reproducibility the
    event log exists to remove. Asserted here because "bound subset of tools" reads as a
    sandbox, and claiming a security property this cannot honour would be worse than not
    having it: MCP has no mechanism to restrict which tools a client may call.
    """
    from ddflow.surfaces.mcp import TOOLS, Server

    _with(repo, DEBUGGER)
    text = M.render(M.load_macros(repo)["debugger"], repo, {"symptom": "x"})
    for tool in ("ddflow_bug_found", "ddflow_gate_run", "ddflow_bug_fixed"):
        assert tool in text, f"{tool} was declared and never reached the prompt"
    assert "in this order" in text

    # And a tool the macro did NOT list is still callable, which is the honest statement
    # of what `tools` is.
    reply = Server(repo).handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "ddflow_status", "arguments": {}},
        }
    )
    assert "ddflow_status" in TOOLS
    assert reply["result"]["isError"] is False, "an unlisted tool was blocked — see docstring"


# -- one bad block must not take the list down ---------------------------------------------


def test_a_malformed_macro_does_not_break_prompts_list(repo):
    """The shipped commands are still there, and asking for the broken one by name reports
    the error. Losing the whole list to one bad block is how a feature gets switched off."""
    from ddflow.surfaces.mcp import Server

    _with(repo, '\n[[macro]]\nname = "bad"\nprompt = "x"\nnope = 1\n')
    listed = Server(repo).handle({"jsonrpc": "2.0", "id": 1, "method": "prompts/list"})
    names = [p["name"] for p in listed["result"]["prompts"]]
    assert "bug-hunt" in names, names
    assert "bad" not in names, names

    got = Server(repo).handle(
        {"jsonrpc": "2.0", "id": 2, "method": "prompts/get", "params": {"name": "bad"}}
    )
    assert "error" in got, got
    assert "unknown prompt" in got["error"]["message"], got["error"]


def test_a_macro_is_read_from_its_own_file_too(repo):
    """`.ddflow/macros.toml`, for operators who prefer to split it out — the same two-file
    precedence as reviewers and companions."""
    run_cli(repo, "init")
    (repo / ".ddflow" / "macros.toml").write_text(
        '[[macro]]\nname = "incident"\ntitle = "Incident mode"\nprompt = "Stabilise first."\n'
    )
    assert "incident" in M.load_macros(repo)
    assert "Stabilise" in P.resolve_command("incident", repo).text


def test_the_config_loader_passes_over_the_macro_table(repo):
    """`[[macro]]` in `.ddflow/config.toml` must not be read as an unknown SECTION.

    `companion` was missing from that list once, which is why companions could only be
    configured in their own file: putting the block in the obvious place made the whole
    config unreadable.
    """
    from ddflow.config import Config

    _with(repo, DEBUGGER)
    cfg = Config.load(repo)  # would raise "unknown config section [macro]"
    assert cfg is not None
    assert "macro" in Config._FOREIGN_TABLES
