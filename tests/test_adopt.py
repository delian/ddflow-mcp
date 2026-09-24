"""Adoption into a real project, per agent. These are the paths a new user hits first."""

from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from orchard.adopt import AGENT_TARGETS, NATIVE_RULES


def test_every_agent_has_a_delta_document():
    templates = Path(__file__).resolve().parents[1] / "orchard" / "templates" / "drivers"
    for key, (delta, _cfg) in AGENT_TARGETS.items():
        assert (templates / "deltas" / delta).is_file(), f"{key} has no delta doc"


@pytest.mark.parametrize("agent", sorted(AGENT_TARGETS))
def test_adopt_writes_a_usable_mcp_config(repo, agent):
    assert run_cli(repo, "adopt", "--agents", agent)[0] == 0
    _delta, cfg_rel = AGENT_TARGETS[agent]
    cfg = repo / cfg_rel
    assert cfg.is_file(), f"{agent}: {cfg_rel} was not written"
    if cfg.suffix == ".toml":
        data = tomllib.loads(cfg.read_text())
        entry = data["mcp_servers"]["orchard"]
    else:
        data = json.loads(cfg.read_text())
        field = "servers" if "servers" in data else "mcpServers"
        entry = data[field]["orchard"]
    assert entry["command"], f"{agent}: no launch command"
    assert isinstance(entry.get("args", []), list)


def test_cursor_gets_an_always_applied_project_rule(repo):
    """Cursor's precedence puts Project Rules ABOVE AGENTS.md.

    Writing only AGENTS.md would leave the queue discipline as the lowest-priority
    instruction in the stack, and claim-before-you-edit is not a rule that should
    depend on the model choosing to load it.
    """
    assert run_cli(repo, "adopt", "--agents", "cursor")[0] == 0
    mdc = repo / NATIVE_RULES["cursor"]
    assert mdc.is_file(), "no .cursor/rules/*.mdc written"
    text = mdc.read_text()
    assert text.startswith("---\n"), "missing YAML frontmatter"
    front = text.split("---", 2)[1]
    assert "alwaysApply: true" in front
    assert "description:" in front
    # Same text as AGENTS.md, from one source -- two copies that can disagree is the
    # failure this whole project keeps designing against.
    body = text.split("---", 2)[2].strip()
    agents_md = (repo / "AGENTS.md").read_text()
    assert body.splitlines()[0] in agents_md
    assert "ORCHARD:BEGIN" not in text, "the managed marker leaked into the .mdc"


def test_adopt_is_idempotent(repo):
    run_cli(repo, "adopt", "--agents", "claude,cursor")
    first = (repo / "AGENTS.md").read_text()
    run_cli(repo, "adopt", "--agents", "claude,cursor")
    assert (repo / "AGENTS.md").read_text() == first
    assert first.count("ORCHARD:BEGIN") == 1


def test_adopt_preserves_existing_mcp_servers(repo):
    (repo / ".cursor").mkdir()
    (repo / ".cursor" / "mcp.json").write_text(
        json.dumps({"mcpServers": {"context7": {"command": "npx", "args": ["-y", "x"]}}})
    )
    run_cli(repo, "adopt", "--agents", "cursor")
    data = json.loads((repo / ".cursor" / "mcp.json").read_text())
    assert "context7" in data["mcpServers"], "clobbered an existing server"
    assert "orchard" in data["mcpServers"]


def test_adopt_keeps_the_users_own_prose(repo):
    (repo / "AGENTS.md").write_text("# My Project\n\nMy own notes that must survive.\n")
    run_cli(repo, "adopt", "--agents", "claude")
    text = (repo / "AGENTS.md").read_text()
    assert "My own notes that must survive." in text
    assert "ORCHARD:BEGIN" in text


def test_the_project_instruction_block_stays_short(repo):
    """The per-project text must stay small: the MCP tool descriptions carry the how,
    and a long second copy of that is a copy that drifts from the one actually read."""
    run_cli(repo, "adopt", "--agents", "claude")
    text = (repo / "AGENTS.md").read_text()
    block = text.split("ORCHARD:BEGIN")[1].split("ORCHARD:END")[0]
    words = len(block.split())
    assert words < 400, f"the managed block has grown to {words} words"
    for must in ("orchard_brief", "Claim before you edit", "unavailable", "Exit codes"):
        assert must in block, f"the block lost {must!r}"
