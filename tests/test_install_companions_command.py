"""`install-companions` — the command that has the agent install the companion tools.

What makes it safe to ship is not the installing, it is the refusals around it: consent
per install, the registry's command verbatim, proof by re-probing rather than by the
installer's exit code, and a decline recorded as `unavailable` rather than a gate passed
unaided. Each test pins one of those, or a claim the prompt makes about ddflow itself.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from test_mcp import proj, rpc  # noqa: F401 -- `proj` is a fixture

from ddflow.services import prompts as P

NAME = "install-companions"


def _text(scope: str = "") -> str:
    return P.render(P.resolve_command(NAME), scope=scope)


def test_it_is_an_mcp_prompt_with_a_scope(proj):  # noqa: F811
    r = rpc(proj, [{"jsonrpc": "2.0", "id": 1, "method": "prompts/list"}])
    by_name = {p["name"]: p for p in r[0]["result"]["prompts"]}
    assert [a["name"] for a in by_name[NAME].get("arguments", [])] == ["scope"]
    r = rpc(
        proj,
        [
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "prompts/get",
                "params": {"name": NAME, "arguments": {"scope": "context7"}},
            }
        ],
    )
    text = r[0]["result"]["messages"][0]["content"]["text"]
    assert "only context7" in text and "{{" not in text


def test_the_cli_shows_it_too(repo):
    code, out, _ = run_cli(repo, "prompts", "show", NAME)
    assert code == 0 and "ddflow_companions_add" in out


@pytest.mark.parametrize(
    "must_say",
    [
        "consents to every install",
        "verbatim",  # the registry's command, not a substitute
        "is not an installed tool",  # exit 0 proves nothing; the probe does
        "dry_run=true",  # the config entry is shown before it is written
        "never `passed` unaided",
        "hand that one to the operator",  # sudo / credentials are not worked around
    ],
)
def test_the_consent_and_evidence_rules_are_in_the_prompt(must_say):
    assert must_say in _text()


def test_an_unscoped_run_leaves_opt_in_companions_alone():
    assert "opt-in" in _text() and "opt-in" not in _text("roborev")


def test_every_tool_it_names_exists():
    from ddflow.surfaces.mcp import TOOLS

    named = set(re.findall(r"`?(ddflow_[a-z_]+)`?", P.resolve_command(NAME).text))
    assert named, "names no tools"
    assert named <= set(TOOLS), f"unknown tools: {sorted(named - set(TOOLS))}"


def test_every_companion_field_it_reads_is_one_the_tool_returns(repo):
    """The prompt sorts rows by `state` and `kind`, quotes `title`, `install`, `url`,
    `gates` and `detail`, and judges liveness by `gate_coverage`. A field it names that
    the tool does not return leaves it reading nothing (rubber-duck on B187: it quoted a
    `why` that only the human renderer shows)."""
    code, out, err = run_cli(repo, "--json", "companions", "list")
    assert code in (0, 2), err
    data = json.loads(out)
    row = data["companions"][0]
    text = P.resolve_command(NAME).text
    quoted = set(re.findall(r"`([a-z_]+)`", text))
    for field in quoted & {"why", "title", "install", "url", "gates", "detail", "kind", "state"}:
        assert field in row, f"the prompt reads `{field}`, which the tool does not return"
    for field in quoted & {"gate_coverage", "uncovered_gates"}:
        assert field in data, field
    for state in ("registered", "installed", "missing", "unknown"):
        assert f"`{state}`" in text


def test_a_stale_config_entry_for_a_cli_tool_is_not_called_live(repo):
    """Rubber-duck on B187: `state` is `registered` for a cli companion with a leftover
    MCP entry even when the tool is absent. The prompt must judge by `gate_coverage`,
    and that must not list it."""
    (repo / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"roborev": {"command": "roborev", "args": ["mcp"]}}})
    )
    import os
    import shutil

    git_dir = str(Path(shutil.which("git")).parent)
    if shutil.which("roborev", path=git_dir):
        pytest.skip("roborev shares git's directory; cannot hide it")
    env_path = os.environ["PATH"]
    try:
        os.environ["PATH"] = git_dir  # git stays reachable; roborev does not
        _code, out, err = run_cli(repo, "--json", "companions", "list")
    finally:
        os.environ["PATH"] = env_path
    data = json.loads(out or err)
    row = next(r for r in data["companions"] if r["id"] == "roborev")
    assert row["state"] == "registered"  # the misleading field the prompt must not trust
    assert "roborev" not in data["gate_coverage"].get("standards", [])
    text = P.resolve_command(NAME).text
    assert "live exactly when its id appears in `gate_coverage`" in text
    assert "| `cli` | `registered` | **install** it" in text
