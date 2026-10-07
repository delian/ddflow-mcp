"""The CLI can get the prompt MCP `prompts/get` gives (bug B5a2a2933c9).

MCP rendered a workflow command or `[[macro]]` through one path -- every declared argument
bound, a macro's parameters REQUIRED and its tool preamble on top -- while the CLI had
only `prompts show`, which prints the SOURCE (`{% if scope %}` tags; a macro with no
parameter check and no preamble). `show` keeps that contract (eject and the managed
`/implement` command rely on it); `prompts get` and the `ddflow_prompts` tool's `get` now
render through the same `prompts.render_command` as `prompts/get`.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.surfaces.mcp import Server

OK, FAIL = 0, 1

MACRO = '''
[[macro]]
name = "debugger"
title = "Enter debugger mode"
params = ["symptom"]
tools = ["ddflow_bug_found", "ddflow_gate_run"]
prompt = """
You are debugging: {{ symptom }}
"""
'''


def _with_macro(repo: Path) -> None:
    run_cli(repo, "init")
    cfg = repo / ".ddflow" / "config.toml"
    cfg.write_text(cfg.read_text() + MACRO)


def _mcp_get(repo: Path, name: str, args: dict | None = None) -> dict:
    params = {"name": name, **({"arguments": args} if args else {})}
    return Server(repo).handle(
        {"jsonrpc": "2.0", "id": 1, "method": "prompts/get", "params": params}
    )


def _mcp_text(repo: Path, name: str, args: dict | None = None) -> str:
    got = _mcp_get(repo, name, args)
    assert "result" in got, got
    return got["result"]["messages"][0]["content"]["text"]


def test_a_macro_shows_on_the_cli_exactly_as_mcp_serves_it(repo):
    _with_macro(repo)
    want = _mcp_text(repo, "debugger", {"symptom": "flaky merge"})
    assert "ddflow_bug_found" in want and "flaky merge" in want, want

    code, out, err = run_cli(repo, "prompts", "get", "debugger", "--arg", "symptom=flaky merge")

    assert code == OK, err
    assert out.rstrip("\n") == want.rstrip("\n")


def test_a_macro_missing_its_parameter_is_refused_on_the_cli_as_on_mcp(repo):
    _with_macro(repo)
    assert "symptom" in _mcp_get(repo, "debugger")["error"]["message"]

    code, out, err = run_cli(repo, "prompts", "get", "debugger")

    assert code == FAIL, out
    assert "symptom" in err, err


def test_a_shipped_command_is_got_rendered_not_as_template_source(repo):
    run_cli(repo, "init")
    want = _mcp_text(repo, "implement")
    code, out, err = run_cli(repo, "prompts", "get", "implement")
    assert code == OK, err
    assert "{%" not in out and "{{" not in out, "the CLI printed template tags"
    assert out.rstrip("\n") == want.rstrip("\n")

    scoped = _mcp_text(repo, "implement", {"scope": "P-unify"})
    code, out, err = run_cli(repo, "prompts", "get", "implement", "--arg", "scope=P-unify")
    assert code == OK, err
    assert out.rstrip("\n") == scoped.rstrip("\n")


def test_the_mcp_prompts_tool_renders_the_same_text(repo):
    _with_macro(repo)
    want = _mcp_text(repo, "debugger", {"symptom": "x"})
    got = Server(repo).handle(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "ddflow_prompts",
                "arguments": {"action": "get", "name": "debugger", "arg": ["symptom=x"]},
            },
        }
    )
    text = got["result"]["content"][0]["text"]
    assert want.strip() in text, text


def test_show_still_prints_the_source(repo):
    """`show` is the source, as `eject` writes it: the managed `/implement` command reads
    it and substitutes `{{ scope }}` itself."""
    run_cli(repo, "init")
    code, out, err = run_cli(repo, "prompts", "show", "implement")
    assert code == OK, err
    assert "{% if scope %}" in out


def test_get_refuses_a_machinery_template_and_names_show(repo):
    run_cli(repo, "init")
    code, out, err = run_cli(repo, "prompts", "get", "mcp_instructions")
    assert code == FAIL, out
    assert "prompts show mcp_instructions" in err, err


def test_a_malformed_argument_is_refused(repo):
    run_cli(repo, "init")
    code, out, err = run_cli(repo, "prompts", "get", "implement", "--arg", "scope")
    assert code == FAIL, out
    assert "KEY=VALUE" in err, err
