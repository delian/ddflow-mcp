"""Before Claude Code compacts the context, ddflow records what the session was doing,
and hands it back after compaction (B195)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from conftest import run_cli

from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.services import compaction as CP

ROOT = Path(__file__).resolve().parents[1]


def _hook(repo, sub, payload):
    """Run `ddflow hooks <sub>` as Claude Code does: the hook JSON on stdin."""
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    return subprocess.run(
        [sys.executable, "-m", "ddflow", "--repo", str(repo), "hooks", sub],
        input=json.dumps(payload), capture_output=True, text=True, env=env, timeout=120,
        check=False,
    )  # fmt: skip


def _transcript(tmp_path):
    rows = [
        {"type": "user", "message": {"role": "user", "content": "refactor the ledger"}},
        {"type": "assistant", "message": {"content": [
            {"type": "text", "text": "Splitting ledger.py into money and account."},
            {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}},
        ]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "content": "a.py"}]}},
        {"type": "user", "isMeta": True, "message": {"content": "<command-name>/x</command-name>"}},
        {"type": "user", "message": {"content": "use token=supersecret123 for the API"}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "Next: tests."}]}},
    ]  # fmt: skip
    p = tmp_path / "t.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\nnot json\n")
    return p


def _notes(repo):
    st = fold(EventLog(repo).read_all(), strict=False)
    return [
        n
        for s in st.sessions.values()
        for n in s.notes
        if n.get("source", "").startswith("compaction")
    ]


def test_the_digest_keeps_words_not_tool_traffic_oldest_first(tmp_path):
    text = CP.digest(_transcript(tmp_path), 2000)
    lines = text.splitlines()
    assert lines[0] == "operator: refactor the ledger"
    assert lines[1] == "agent: Splitting ledger.py into money and account."
    assert lines[-1] == "agent: Next: tests."
    assert "tool_result" not in text and "a.py" not in text and "command-name" not in text


def test_the_digest_stays_within_its_budget(tmp_path):
    text = CP.digest(_transcript(tmp_path), 60)
    assert len(text) <= 60 and text.endswith("agent: Next: tests.")


def test_pre_compact_records_a_redacted_note_and_is_silent(repo, tmp_path):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", "a.py")
    p = _hook(repo, "pre-compact", {
        "session_id": "abc-123", "transcript_path": str(_transcript(tmp_path)),
        "hook_event_name": "PreCompact", "trigger": "manual",
    })  # fmt: skip
    assert p.returncode == 0 and p.stdout == "", (p.stdout, p.stderr)
    (note,) = _notes(repo)
    assert note["source"] == "compaction:manual"
    assert "refactor the ledger" in note["text"] and "supersecret123" not in note["text"]


def test_a_broken_payload_never_blocks_compaction(repo):
    run_cli(repo, "init")
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    p = subprocess.run(
        [sys.executable, "-m", "ddflow", "--repo", str(repo), "hooks", "pre-compact"],
        input="{not json", capture_output=True, text=True, env=env, check=False,
    )  # fmt: skip
    assert p.returncode == 0 and p.stdout == "" and _notes(repo) == []


def test_session_start_after_compaction_hands_the_digest_back(repo, tmp_path):
    run_cli(repo, "init")
    _hook(repo, "pre-compact", {"session_id": "abc-123", "trigger": "auto",
                                "transcript_path": str(_transcript(tmp_path))})  # fmt: skip
    out = _hook(repo, "session-start", {"session_id": "abc-123", "source": "compact"}).stdout
    assert "## Before this compaction" in out and "refactor the ledger" in out
    other = _hook(repo, "session-start", {"session_id": "abc-123", "source": "startup"}).stdout
    assert "Before this compaction" not in other


def test_session_start_asks_for_a_record_when_none_landed(repo):
    run_cli(repo, "init")
    out = _hook(repo, "session-start", {"session_id": "zzz-9", "source": "compact"}).stdout
    assert "No record of what this session was doing" in out and "ddflow session note" in out


def test_install_adds_the_pre_compact_hook_and_uninstall_removes_it(repo):
    run_cli(repo, "init")
    assert run_cli(repo, "hooks", "install", "--claude")[0] == 0
    hooks = json.loads((repo / ".claude" / "settings.json").read_text())["hooks"]
    assert any("hooks pre-compact" in h["command"] for g in hooks["PreCompact"] for h in g["hooks"])
    assert "PreCompact hook: installed" in run_cli(repo, "hooks", "status")[1]
    run_cli(repo, "hooks", "uninstall", "--claude")
    hooks = json.loads((repo / ".claude" / "settings.json").read_text()).get("hooks", {})
    assert not any(
        "hooks pre-compact" in h["command"] for g in hooks.get("PreCompact", []) for h in g["hooks"]
    )


def test_zero_budget_turns_it_off(repo, tmp_path):
    run_cli(repo, "init")
    run_cli(repo, "config", "session.compaction_digest_chars", "0")
    _hook(repo, "pre-compact", {"session_id": "abc", "trigger": "auto",
                                "transcript_path": str(_transcript(tmp_path))})  # fmt: skip
    assert _notes(repo) == []
