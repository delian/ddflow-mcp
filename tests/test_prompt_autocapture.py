"""Every operator prompt is recorded by the harness hook, not by the agent's goodwill.

Today a prompt is kept only if the agent calls session_prompt. A UserPromptSubmit hook
(Claude Code) / BeforeAgent hook (Gemini CLI) runs `ddflow hooks prompt` with the
harness's JSON on stdin; it must record one redacted session.prompt, never leak a secret
to the log, never break the user's turn, and not double-record with an agent that also
records the prompt.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

SECRET = "sk-abcdefghijklmnop1234567890"


def _hook(repo: Path, payload, *extra: str, raw: str | None = None, agent: str = ""):
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
    args = [sys.executable, "-m", "ddflow", "--repo", str(repo)]
    if agent:
        args += ["--agent", agent]
    p = subprocess.run(
        [*args, "hooks", "prompt", *extra],
        input=raw if raw is not None else json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    return p.returncode, p.stdout, p.stderr


def _events(repo: Path, kind: str) -> list[dict]:
    out = []
    for shard in (repo / ".ddflow" / "events").glob("*.jsonl"):
        for line in shard.read_text().splitlines():
            ev = json.loads(line)
            if ev["kind"] == kind:
                out.append(ev)
    return out


def _all_text(repo: Path) -> str:
    return "".join(
        p.read_bytes().decode("utf-8", "replace")
        for p in (repo / ".ddflow").rglob("*")
        if p.is_file()
    )


def test_hook_json_records_one_prompt_in_a_session_keyed_on_the_harness_id(repo):
    run_cli(repo, "init")
    code, out, err = _hook(
        repo, {"session_id": "abc-123", "prompt": "build the thing", "hook_event_name": "x"}
    )
    assert code == 0, err
    assert out == "", "Claude Code adds ANY stdout to the model's context"
    prompts = _events(repo, "session.prompt")
    assert [p["data"]["text"] for p in prompts] == ["build the thing"]
    assert prompts[0]["subject"] == "h-abc-123"
    # The same conversation reuses its session: one session.started, two prompts.
    _hook(repo, {"session_id": "abc-123", "prompt": "now the other thing"})
    assert len(_events(repo, "session.started")) == 1
    assert len(_events(repo, "session.prompt")) == 2


def test_a_secret_never_reaches_the_log(repo):
    run_cli(repo, "init")
    _code, out, err = _hook(repo, {"session_id": "s1", "prompt": f"use key {SECRET} please"})
    assert SECRET not in _all_text(repo)
    assert SECRET not in out and SECRET not in err
    (p,) = _events(repo, "session.prompt")
    assert "[REDACTED]" in p["data"]["text"] and p["data"]["redactions"] >= 1


def test_a_failing_ddflow_never_breaks_the_prompt(repo, tmp_path):
    # Not a ddflow repo at all, garbage stdin, empty stdin, a non-object, no prompt.
    bare = tmp_path / "bare"
    bare.mkdir()
    for raw in ("not json", "", "[1,2]", '{"session_id": "x"}', '{"prompt": 5}'):
        code, out, _err = _hook(repo, None, raw=raw)
        assert code == 0 and out == "", raw
    code, _o, _e = _hook(bare, {"session_id": "s", "prompt": "hi"})
    assert code == 0


def test_a_silent_stdin_does_not_hang_the_turn(repo):
    run_cli(repo, "init")
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
    p = subprocess.Popen(
        [sys.executable, "-m", "ddflow", "--repo", str(repo), "hooks", "prompt"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    try:
        assert p.wait(timeout=60) == 0  # stdin stays open and silent
    finally:
        p.kill()


def test_no_duplicate_when_the_agent_also_records_the_prompt(repo):
    run_cli(repo, "init")
    _hook(repo, {"session_id": "s1", "prompt": "please do X"})
    code, _o, err = run_cli(repo, "session", "prompt", "s-agent", "--text", "please do X")
    assert code == 0, err
    assert len(_events(repo, "session.prompt")) == 1
    # A different prompt from the agent is still recorded.
    run_cli(repo, "session", "prompt", "s-agent", "--text", "something else")
    assert len(_events(repo, "session.prompt")) == 2


def test_the_same_hook_firing_twice_records_once(repo):
    run_cli(repo, "init")
    _hook(repo, {"session_id": "s1", "prompt": "go"})
    _hook(repo, {"session_id": "s1", "prompt": "go"})
    assert len(_events(repo, "session.prompt")) == 1


def test_install_claude_adds_the_prompt_hook_beside_the_operators_own(repo):
    run_cli(repo, "init")
    (repo / ".claude").mkdir()
    theirs = {
        "hooks": {"UserPromptSubmit": [{"hooks": [{"type": "command", "command": "echo hi"}]}]}
    }
    (repo / ".claude" / "settings.json").write_text(json.dumps(theirs))
    code, _o, err = run_cli(repo, "hooks", "install", "--claude")
    assert code == 0, err
    data = json.loads((repo / ".claude" / "settings.json").read_text())
    cmds = [h["command"] for g in data["hooks"]["UserPromptSubmit"] for h in g["hooks"]]
    assert "echo hi" in cmds and sum("hooks prompt" in c for c in cmds) == 1
    run_cli(repo, "hooks", "install", "--claude")
    data = json.loads((repo / ".claude" / "settings.json").read_text())
    assert (
        sum(
            "hooks prompt" in h["command"]
            for g in data["hooks"]["UserPromptSubmit"]
            for h in g["hooks"]
        )
        == 1
    )
    _c, out, _e = run_cli(repo, "hooks", "status")
    assert "Claude Code: installed" in out
    run_cli(repo, "hooks", "uninstall", "--claude")
    data = json.loads((repo / ".claude" / "settings.json").read_text())
    assert data["hooks"]["UserPromptSubmit"] == theirs["hooks"]["UserPromptSubmit"]


def test_gemini_hook_prints_json_and_installs_into_gemini_settings(repo):
    run_cli(repo, "init")
    code, out, err = _hook(repo, {"session_id": "g1", "prompt": "hello"}, "--gemini")
    assert code == 0, err
    assert json.loads(out) == {}
    assert len(_events(repo, "session.prompt")) == 1
    code, _o, err = run_cli(repo, "hooks", "install", "--gemini")
    assert code == 0, err
    data = json.loads((repo / ".gemini" / "settings.json").read_text())
    assert "hooks prompt --gemini" in data["hooks"]["BeforeAgent"][0]["hooks"][0]["command"]


def test_adopt_installs_the_prompt_hook_for_claude(repo):
    code, _o, err = run_cli(repo, "adopt", "--agents", "claude")
    assert code == 0, err
    data = json.loads((repo / ".claude" / "settings.json").read_text())
    assert any(
        "hooks prompt" in h["command"]
        for g in data["hooks"]["UserPromptSubmit"]
        for h in g["hooks"]
    )


def test_parallel_sessions_typing_the_same_words_are_both_recorded(repo):
    # The hook's double-fire guard is per session: B's "continue" a moment after A's is kept.
    run_cli(repo, "init")
    _hook(repo, {"session_id": "A", "prompt": "continue"})
    _hook(repo, {"session_id": "B", "prompt": "continue"})
    assert len(_events(repo, "session.prompt")) == 2


def test_distinct_harness_ids_that_sanitise_alike_stay_distinct_sessions():
    from ddflow.services.sessions import harness_session_id

    assert harness_session_id("conv/abc") != harness_session_id("convabc")
    assert harness_session_id("abc-123") == "h-abc-123"
    assert harness_session_id("x" * 80) != harness_session_id("x" * 81)


def test_uninstall_with_no_prompt_hook_in_the_file_is_a_quiet_no_op(repo):
    run_cli(repo, "init")
    (repo / ".claude").mkdir()
    only = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo"}]}]}}
    (repo / ".claude" / "settings.json").write_text(json.dumps(only))
    code, _o, err = run_cli(repo, "hooks", "uninstall", "--claude")
    assert code == 0, err
    assert json.loads((repo / ".claude" / "settings.json").read_text()) == only


def test_hook_stdin_falls_back_to_a_thread_read_when_select_cannot_take_the_fd(monkeypatch):
    import select
    import time

    from ddflow.surfaces.commands import setup as S

    r, w = os.pipe()
    os.write(w, '{"prompt": "caf\u00e9"}'.encode())  # written, and the pipe left OPEN

    class _Stdin:
        def isatty(self):
            return False

        def fileno(self):
            return r

    called = []

    def boom(*a, **k):
        called.append(1)
        raise OSError("select only takes sockets here")

    monkeypatch.setattr(select, "select", boom)
    monkeypatch.setattr(sys, "stdin", _Stdin())
    t0 = time.monotonic()
    try:
        assert json.loads(S._hook_stdin(timeout_s=1)) == {"prompt": "caf\u00e9"}
    finally:
        os.close(w)
        os.close(r)
    assert time.monotonic() - t0 < 5
    assert called, "the fallback ran for some reason other than select refusing the fd"
