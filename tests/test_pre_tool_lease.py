"""The pre_tool hook door enforces lease globs for file writes (B20429bf39a).

`ddflow hooks run pre_tool` used to check only forbidden Bash commands: a tool call that
wrote a file slipped through without a lease-glob check, and plugin-style harnesses (kilo,
opencode) had no plan, so `ddflow hooks run pre_tool --harness kilo` reached no entry point at
all. The project door now reuses the lane's lease check (`lanehooks_check.verdict`) and
`plan_for` resolves plugin harnesses too.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ddflow.api import setup as AS
from ddflow.services import hookio
from ddflow.services.swarm import lanehooks_check as CK


def test_plugin_harnesses_have_a_hook_entry_point():
    for hid in ("kilo", "opencode"):
        plan = hookio.plan_for(hid)
        assert plan is not None, hid
        assert plan.normalizer and plan.emitter, hid


def _refuse_lane(repo, agent):
    return (["src/a.py"], ["CHANGELOG.md"])


def test_pre_tool_refuses_a_write_outside_the_lease(repo, monkeypatch):
    monkeypatch.setattr(CK, "_mine", _refuse_lane)
    out = AS.hooks(
        repo,
        action="run",
        event="pre_tool",
        harness="claude",
        agent="lane-1",
        stdin=json.dumps(
            {"tool_name": "Edit", "tool_input": {"file_path": "src/b.py"}, "cwd": str(repo)}
        ),
    )
    assert out.data["result"] == "denied"
    assert "outside the item's lease globs" in out.data["stdout"]


def test_pre_tool_allows_a_write_inside_the_lease(repo, monkeypatch):
    monkeypatch.setattr(CK, "_mine", _refuse_lane)
    out = AS.hooks(
        repo,
        action="run",
        event="pre_tool",
        harness="claude",
        agent="lane-1",
        stdin=json.dumps(
            {"tool_name": "Write", "tool_input": {"file_path": "src/a.py"}, "cwd": str(repo)}
        ),
    )
    assert out.data["result"] == "allowed"
    assert out.data["stdout"] == ""


def test_pre_tool_does_not_refuse_a_writer_without_a_lease(repo, monkeypatch):
    """With no live lease the project door stays open (the commit hook governs that case): it
    refuses only a write outside lease globs the agent actually holds."""
    monkeypatch.setattr(CK, "_mine", lambda repo, agent: ([], []))
    out = AS.hooks(
        repo,
        action="run",
        event="pre_tool",
        harness="claude",
        agent="lane-1",
        stdin=json.dumps(
            {"tool_name": "Edit", "tool_input": {"file_path": "src/b.py"}, "cwd": str(repo)}
        ),
    )
    assert out.data["result"] == "allowed"
    assert out.data["stdout"] == ""
