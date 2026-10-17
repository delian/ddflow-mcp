"""The PreToolUse guard: commands an agent is told never to run are refused before they run
(DX-forbidden-commands-hook). Three subagents ran `pkill` against a brief that said never."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ddflow.config import Config
from ddflow.services import forbidden as FB


def _check(cmd: str, env: dict | None = None, **kw):
    return FB.check(cmd, Config(), env=env or {}, **kw)


@pytest.mark.parametrize(
    "cmd",
    [
        "pkill -f pytest",
        "sudo pkill python",
        "cd x && killall node",
        "git stash",
        "git -C /tmp/x stash push -u",
        "git push origin main",
        "git push --force-with-lease",
        "rm -fr .ddflow",
        "rm -rf ./.ddflow/events",
        "ddflow merge X --force",
        "FOO=1 bash -c 'pkill foo'",
        "true; git stash pop",
        "echo hi | xargs pkill",
        "sudo -u root pkill foo",
        "sudo -n pkill -f worker",
        "sudo -n bash -c 'pkill -f worker'",
        "time -p pkill worker",
        "xargs -t pkill",
        "bash -c \"bash -c 'pkill -f worker'\"",
        "cat <<EOF-2 >/dev/null\nx\nEOF\npkill foo",
        "pkill -f worker # ddflow review --force --reason",
        "nice -n 5 pkill foo",
        "env -u HOME pkill foo",
        "xargs -n 1 pkill",
        'python -c "print(1<<3)"\npkill -f myserver',
        'git commit -m "a<<b"\npkill foo',
        'grep x <<< "$y"\npkill foo',
        "ddflow review X --force",
    ],
)
def test_refused(cmd):
    r = _check(cmd, cwd="/nonexistent")
    assert r is not None, cmd
    assert "Instead:" in r.message()


@pytest.mark.parametrize(
    "cmd",
    [
        "git log --grep pkill",
        'git commit -m "git stash is banned"',
        "git commit -m push",
        "git stash-ish",
        "grep -r pkill docs",
        "echo pkill",
        'ddflow review X --force --reason "an extra round"',
        'ddflow review X --reason "an extra round" --force',
        "rm -rf build",
        "kill 1234",
        "cat <<EOF-2\npkill -f worker\nEOF-2",
        "cat <<'END.JSON'\npkill x\nEND.JSON",
        "cat <<'EOF'\npkill x\nEOF",
        "ls",
    ],
)
def test_allowed(cmd):
    assert _check(cmd) is None, cmd


def test_reset_hard_only_on_primary(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    assert _check("git reset --hard HEAD", cwd=tmp_path) is not None
    assert _check("git reset --soft HEAD~1", cwd=tmp_path) is None
    assert _check("git -C /elsewhere reset --hard", cwd=tmp_path / "nope") is not None
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            "-c",
            "user.name=a",
            "-c",
            "user.email=a@b",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "x",
        ],
        check=True,
    )
    wt = tmp_path / "wt"
    subprocess.run(["git", "-C", str(tmp_path), "worktree", "add", "-q", str(wt)], check=True)
    assert _check("git reset --hard HEAD", cwd=wt) is None
    # from a worktree, a command that names another checkout is still judged as the primary
    assert _check(f"git -C {tmp_path} reset --hard", cwd=wt) is not None
    assert _check(f"cd {tmp_path} && git reset --hard", cwd=wt) is not None
    assert _check(f"git --git-dir={tmp_path}/.git reset --hard", cwd=wt) is not None
    assert _check(f"GIT_DIR={tmp_path}/.git git reset --hard", cwd=wt) is not None
    assert _check("make -C build && git reset --hard", cwd=wt) is None
    assert _check('echo GIT_DIR=x && git commit -m "GIT_DIR=y" && git reset --hard', cwd=wt) is None


def test_operator_lifts_per_session_and_config():
    assert _check("pkill x", {FB.ENV_ALLOW: "pkill"}) is None
    assert _check("pkill x", {FB.ENV_ALLOW: "git stash"}) is not None
    assert _check("git reset --hard", {FB.ENV_ALLOW: "git reset --hard @primary"}) is None
    assert _check("git push", {FB.ENV_ALLOW: "all"}) is None
    cfg = Config()
    cfg.hooks.forbidden_commands = []
    assert FB.check("pkill x", cfg, env={}) is None


def _hook(repo: Path, payload: dict) -> str:
    p = subprocess.run(
        [sys.executable, "-m", "ddflow", "hooks", "run", "pre_tool", "--harness", "claude"],
        cwd=repo,
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env={
            **__import__("os").environ,
            "DDFLOW_HOOKS_ALLOW": "",
            "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
        },
    )
    assert p.returncode == 0, p.stderr
    return p.stdout


def _repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run([sys.executable, "-m", "ddflow", "init"], cwd=tmp_path, capture_output=True)
    return tmp_path


def test_hook_denies_and_records(tmp_path):
    repo = _repo(tmp_path)
    out = _hook(
        repo,
        {"session_id": "s1", "tool_name": "Bash", "tool_input": {"command": "pkill -f pytest"}},
    )
    reply = json.loads(out)["hookSpecificOutput"]
    assert reply["permissionDecision"] == "deny"
    assert "pkill" in reply["permissionDecisionReason"]
    events = [
        json.loads(x)
        for f in (repo / ".ddflow" / "events").rglob("*.jsonl")
        for x in f.read_text().splitlines()
    ]
    assert any(
        e["kind"] == "session.note"
        and str(e["data"].get("source", "")).startswith("forbidden-command")
        for e in events
    )


def test_hook_allows_other_commands_and_tools(tmp_path):
    repo = _repo(tmp_path)
    assert _hook(repo, {"tool_name": "Bash", "tool_input": {"command": "ls"}}) == ""
    assert _hook(repo, {"tool_name": "Edit", "tool_input": {"command": "pkill"}}) == ""
    assert _hook(repo, {}) == ""
