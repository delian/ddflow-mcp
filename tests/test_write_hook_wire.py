"""Wire the write-time lease check into the project hook (LK-write-hook-wire).

The project door's pre-tool hook used to register `matcher = "Bash"` only, so it fired
for no file write: an Edit or Write on a file another agent's lease covers slipped through
to the commit-time check. The matcher must name the write tools, and the refusal must name
the holder and the options (wait, send a suggestion, open a task) a D-lock-lifecycle-answers
refusal promises. The check is turned by `[locks].mode = enforce|log|off`.
"""

from __future__ import annotations

import json

from ddflow.api import setup as AS
from ddflow.services import claudehooks as CH
from ddflow.services.swarm import lanehooks_check as CK


def test_claude_pretool_matcher_covers_the_write_tools():
    """The pre-tool hook must fire for Edit/Write/MultiEdit/NotebookEdit, not just Bash."""
    matcher = CH.spec("claude", "pre-tool").matcher
    for tool in ("Bash", "Edit", "Write", "MultiEdit", "NotebookEdit"):
        assert tool in matcher.split("|"), matcher


def test_verdict_names_the_holder_and_the_options(repo, monkeypatch):
    monkeypatch.setattr(CK, "_mine", lambda repo, agent: (["src/a.py"], []))
    monkeypatch.setattr(CK, "_holder", lambda repo, agent, rel: "agent-2")
    code, msg = CK.verdict(repo, "lane-1", ["src/b.py"], repo)
    assert code == CK.REFUSE
    assert "agent-2" in msg
    assert "ddflow wait" in msg
    assert "`ddflow suggest" in msg
    assert "ddflow task add" in msg


def _write_stdin(repo, path="src/b.py"):
    return json.dumps({"tool_name": "Edit", "tool_input": {"file_path": path}, "cwd": str(repo)})


def test_mode_enforce_refuses_a_write_outside_the_lease(repo, monkeypatch):
    monkeypatch.setenv("DDFLOW_LOCKS_MODE", "enforce")
    monkeypatch.setattr(CK, "_mine", lambda repo, agent: (["src/a.py"], []))
    out = AS.hooks(
        repo,
        action="run",
        event="pre_tool",
        harness="claude",
        agent="lane-1",
        stdin=_write_stdin(repo),
    )
    assert out.data["result"] == "denied"
    assert "outside the item's lease globs" in out.data["stdout"]


def test_mode_log_allows_but_notes_the_refusal(repo, monkeypatch):
    monkeypatch.setenv("DDFLOW_LOCKS_MODE", "log")
    monkeypatch.setattr(CK, "_mine", lambda repo, agent: (["src/a.py"], []))
    out = AS.hooks(
        repo,
        action="run",
        event="pre_tool",
        harness="claude",
        agent="lane-1",
        stdin=_write_stdin(repo),
    )
    assert out.data["result"] == "logged"
    assert out.data["stdout"] == ""
    assert "outside the item's lease globs" in out.data["note"]


def test_mode_off_skips_the_lease_check(repo, monkeypatch):
    monkeypatch.setenv("DDFLOW_LOCKS_MODE", "off")
    monkeypatch.setattr(CK, "_mine", lambda repo, agent: (["src/a.py"], []))
    out = AS.hooks(
        repo,
        action="run",
        event="pre_tool",
        harness="claude",
        agent="lane-1",
        stdin=_write_stdin(repo),
    )
    assert out.data["result"] == "allowed"


def test_mode_log_still_refuses_a_forbidden_bash_command(repo, monkeypatch):
    """`[locks].mode` only tempers the lease-glob check: the independent
    `[hooks].forbidden_commands` refusal must still hold in `log` mode."""
    monkeypatch.setenv("DDFLOW_LOCKS_MODE", "log")
    out = AS.hooks(
        repo,
        action="run",
        event="pre_tool",
        harness="claude",
        agent="lane-1",
        stdin=json.dumps(
            {"tool_name": "Bash", "tool_input": {"command": "git stash"}, "cwd": str(repo)}
        ),
    )
    assert out.data["result"] == "denied"
    assert out.data["stdout"] != ""
