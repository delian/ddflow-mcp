"""`onboard` — attach the MCP server, and the agent takes the project the rest of the way.

Written from two onboardings done by hand (run_nemo_run and home-simulator, 2026-09-29).
Every step pinned here is one that was missed or done wrong at least once there: a leftover
harness worktree, a launch entry pointing into a worktree, a test gate nobody measured,
open work the import dropped, a rulebook still telling agents to tick checkboxes in a file
nothing reads any more, an imported file edited after the cutover.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from test_mcp import proj, rpc  # noqa: F401 -- `proj` is a fixture

from ddflow.services import prompts as P

NAME = "onboard"


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
                "params": {"name": NAME, "arguments": {"scope": "skip the import"}},
            }
        ],
    )
    text = r[0]["result"]["messages"][0]["content"]["text"]
    assert "skip the import" in text and "{{" not in text and "{%" not in text


def test_the_cli_shows_it_too(repo):
    code, out, _ = run_cli(repo, "prompts", "show", NAME)
    assert code == 0 and "ddflow_import" in out


@pytest.mark.parametrize(
    "must_say",
    [
        "--is-ancestor",  # "merged" is proven, not read off a commit message
        "never pop or drop",  # the stash is shared by every worktree
        "removed when that worktree is",  # the launch entry must outlive the tree
        "enabledMcpjsonServers",  # the harness must be allowed to start the server
        "baseline",  # the test gate is measured on an untouched tree
        "import-existing-project",  # the judgement half of the import is reused
        "did NOT import",  # the dropped open work is checked against the handoff
        "--priority",  # the handoff's stated order becomes the queue's
        "forbidden_trailers",  # rulebook conventions become enforced config
        "Freeze what was imported",
        "initialize",  # the server is proven to start from the REGISTERED entry
        "never `git add -A`",
        "ddflow_bug_found",  # a defect in ddflow hit on the way is filed, not worked around
        "add both names to `.ddflow/.gitignore`",  # LAN endpoints are never committed
        "does NOT create `.ddflow/config.toml`",  # the MCP setup path's real behaviour
    ],
)
def test_the_steps_learned_by_hand_are_in_the_prompt(must_say):
    assert must_say in _text()


def test_every_tool_it_names_exists():
    from ddflow.surfaces.mcp import TOOLS

    named = set(re.findall(r"`?(ddflow_[a-z_]+)`?", P.resolve_command(NAME).text))
    assert named, "names no tools"
    assert named <= set(TOOLS), f"unknown tools: {sorted(named - set(TOOLS))}"


def test_every_prompt_it_names_exists():
    named = set(re.findall(r"the `([a-z-]+)` prompt", P.resolve_command(NAME).text))
    assert named, "names no prompts"
    assert named <= set(P.COMMANDS), f"unknown prompts: {sorted(named - set(P.COMMANDS))}"


def test_an_unadopted_repository_is_offered_it_at_the_handshake(tmp_path):
    """Attaching the server is the whole install: the first thing an agent reads in a
    repository that does not use ddflow yet must name the path that onboards it."""
    import subprocess

    from ddflow.surfaces.mcp import _instructions

    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True)
    text = _instructions(tmp_path)
    assert "does not use ddflow yet" in text
    assert "`onboard` prompt" in text


def test_an_adopted_project_with_unimported_history_is_offered_it_too(tmp_path):
    """Adopted, history on disk, nothing in the queue: the import alone leaves the
    rulebook telling agents to write files nothing reads any more."""
    import subprocess

    from ddflow.surfaces.mcp import _instructions

    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True)
    assert run_cli(tmp_path, "init")[0] == 0
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "todo.md").write_text("## P1\n\n- [ ] **P1.1** — work\n")
    text = _instructions(tmp_path)
    assert "the queue is empty" in text
    assert "the `onboard` prompt also cuts the workflow over" in text
