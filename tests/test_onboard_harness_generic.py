"""Onboarding per descriptor: register, approve where the agent needs it, install hooks, say the level."""

from __future__ import annotations

import json

from ddflow.api import onboard as A
from ddflow.services import harnessreg as HR


def _steps(out, hid):
    return {s["step"]: s for r in out.data["harnesses"] if r["id"] == hid for s in r["steps"]}


def test_an_unregistered_server_is_a_todo_and_says_how(repo):
    out = A.onboard(repo, stage="harness", accept=["claude"])
    assert out.exit == 0
    s = _steps(out, "claude")
    assert s["mcp"]["status"] == "todo" and "adopt --agents claude" in s["mcp"]["detail"]


def test_apply_approves_the_server_and_installs_hooks_for_claude(repo):
    (repo / ".mcp.json").write_text(json.dumps({"mcpServers": {"ddflow": {"command": "x"}}}))
    out = A.onboard(repo, stage="harness", accept=["claude"], apply=True)
    s = _steps(out, "claude")
    assert s["approve"]["status"] == "done" and s["hooks"]["status"] == "done"
    settings = json.loads((repo / ".claude/settings.json").read_text())
    assert settings["enabledMcpjsonServers"] == ["ddflow"]
    assert "hooks" in settings
    again = A.onboard(repo, stage="harness", accept=["claude"])
    assert {k: v["status"] for k, v in _steps(again, "claude").items()} == {
        "mcp": "done",
        "approve": "done",
        "hooks": "done",
    }


def test_a_denied_server_is_refused_not_overridden(repo):
    (repo / ".mcp.json").write_text(json.dumps({"mcpServers": {"ddflow": {"command": "x"}}}))
    (repo / ".claude").mkdir()
    (repo / ".claude/settings.json").write_text(json.dumps({"disabledMcpjsonServers": ["ddflow"]}))
    out = A.onboard(repo, stage="harness", accept=["claude"], apply=True)
    assert _steps(out, "claude")["approve"]["status"] == "refused"
    assert out.exit == 1
    kept = json.loads((repo / ".claude/settings.json").read_text())["disabledMcpjsonServers"]
    assert kept == ["ddflow"]


def test_an_agent_with_no_approval_step_or_hooks_says_n_a(repo):
    s = _steps(A.onboard(repo, stage="harness", accept=["aider"]), "aider")
    assert s["mcp"]["status"] == "n/a" and s["approve"]["status"] == "n/a"
    assert s["hooks"]["status"] == "n/a"


def test_a_non_claude_agent_gets_hooks_from_its_descriptor(repo):
    out = A.onboard(repo, stage="harness", accept=["codex"], apply=True)
    assert _steps(out, "codex")["hooks"]["status"] == "done"
    assert (repo / HR.get("codex").hooks.file).is_file()


def test_every_row_names_the_best_ladder_level(repo):
    out = A.onboard(repo, stage="harness", accept=["gemini", "aider"])
    assert {r["id"]: r["level"] for r in out.data["harnesses"]}["gemini"] == "native hook"


def test_an_unknown_agent_is_refused_with_the_list(repo):
    out = A.onboard(repo, stage="harness", accept=["nope"])
    assert out.exit == 1 and "claude" in out.reason
