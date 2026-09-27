"""The Claude Code SessionStart hook: the brief arrives whether or not anyone asks.

Everything else about session start is a request -- "call ddflow_brief first" -- and a
request is what a model drops after a context compaction. A SessionStart hook runs
because Claude Code runs it, and its stdout lands in the session. The projects this was
built for already run SessionStart hooks of their own, so the other property that
matters is that ours is added beside them and removed alone.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.surfaces.mcp import Server

OK, FAIL, NOTHING = 0, 1, 2

EXISTING = {
    "permissions": {"defaultMode": "bypassPermissions"},
    "model": "opus[1m]",
    "hooks": {
        "SessionStart": [
            {
                "hooks": [
                    {
                        "type": "command",
                        "command": "python scripts/check_worktree_sync.py --hook || true",
                    },
                    {
                        "type": "command",
                        "command": "python scripts/bootstrap_tools.py --hook || true",
                    },
                ]
            }
        ],
        "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "echo hi"}]}],
        # An empty group is the operator's too -- a placeholder, or half an edit.
        "Stop": [{"matcher": "", "hooks": []}],
    },
}


def _settings(repo: Path) -> dict:
    return json.loads((repo / ".claude" / "settings.json").read_text())


def _ours(data: dict) -> list[dict]:
    return [
        h
        for g in data.get("hooks", {}).get("SessionStart", [])
        for h in g.get("hooks", [])
        if "hooks session-start" in h.get("command", "")
    ]


def test_install_adds_the_hook_and_is_idempotent(repo):
    run_cli(repo, "init")
    code, out, err = run_cli(repo, "hooks", "install", "--claude")
    assert code == OK, err
    assert len(_ours(_settings(repo))) == 1
    code, out, _ = run_cli(repo, "hooks", "install", "--claude")
    assert code == OK and "already" in out
    assert len(_ours(_settings(repo))) == 1, "a second install added a second hook"
    _c, out, _e = run_cli(repo, "--json", "hooks", "status")
    assert json.loads(out)["session_hook"] is True


def test_the_operators_own_hooks_and_settings_survive_install_AND_uninstall(repo):
    run_cli(repo, "init")
    (repo / ".claude").mkdir()
    (repo / ".claude" / "settings.json").write_text(json.dumps(EXISTING))
    code, _out, err = run_cli(repo, "hooks", "install", "--claude")
    assert code == OK, err
    data = _settings(repo)
    assert data["permissions"] == EXISTING["permissions"] and data["model"] == "opus[1m]"
    assert data["hooks"]["PreToolUse"] == EXISTING["hooks"]["PreToolUse"]
    theirs = data["hooks"]["SessionStart"][0]["hooks"]
    assert theirs == EXISTING["hooks"]["SessionStart"][0]["hooks"], "their hooks were edited"
    assert len(_ours(data)) == 1

    code, _out, err = run_cli(repo, "hooks", "uninstall", "--claude")
    assert code == OK, err
    assert _settings(repo) == EXISTING, "uninstall did not leave the file as it found it"


def test_a_settings_file_that_is_not_json_is_refused_and_left_alone(repo):
    run_cli(repo, "init")
    (repo / ".claude").mkdir()
    half_written = '{"hooks": {"SessionStart": [ '
    (repo / ".claude" / "settings.json").write_text(half_written)
    code, _out, err = run_cli(repo, "hooks", "install", "--claude")
    assert code == FAIL
    assert "not valid JSON" in err
    assert (repo / ".claude" / "settings.json").read_text() == half_written


def test_the_INSTALLED_command_runs_and_puts_the_brief_and_memory_in_the_session(repo, tmp_path):
    """Not the function: the literal command line written to settings.json, run by a
    shell with none of the test's environment -- which is how Claude Code runs it."""
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "the next thing", "--globs", "a.py")
    run_cli(repo, "memory", "add", "8x H200 on this box")
    run_cli(repo, "hooks", "install", "--claude")
    command = _ours(_settings(repo))[0]["command"]
    # A PATH holding git and NOTHING else: Claude Code's environment is not the test's,
    # and a command that only works because `python3` happens to resolve to something
    # with ddflow's dependencies is a hook that works on the author's machine.
    import shutil

    only_git = tmp_path / "only-git"
    only_git.mkdir()
    (only_git / "git").symlink_to(shutil.which("git"))
    p = subprocess.run(
        ["/bin/sh", "-c", command],
        cwd=repo,
        capture_output=True,
        text=True,
        env={"PATH": str(only_git), "HOME": str(repo)},
        timeout=300,
    )
    assert p.returncode == 0, p.stderr
    assert "ddflow brief" in p.stdout and "T1" in p.stdout
    assert "8x H200 on this box" in p.stdout


def test_session_start_never_fails_even_where_there_is_no_queue(tmp_path):
    """A hook that errors at session start is removed by the operator, and then it
    informs nobody."""
    plain = tmp_path / "plain"
    plain.mkdir()
    subprocess.run(["git", "init", "-q", str(plain)], check=True)
    code, out, _err = run_cli(plain, "hooks", "session-start")
    assert code == OK
    assert "ddflow session start" in out


def test_a_worktree_behind_its_base_is_told_so_and_which_rules_changed(repo):
    run_cli(repo, "init")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "init ddflow"], check=True)
    wt = repo.parent / "wt"
    subprocess.run(
        ["git", "-C", str(repo), "worktree", "add", "-q", "-b", "feat", str(wt)], check=True
    )
    (repo / "AGENTS.md").write_text("# new rule\n")
    subprocess.run(["git", "-C", str(repo), "add", "AGENTS.md"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "rules"], check=True)

    env = {"PYTHONPATH": str(Path(__file__).resolve().parents[1]), "PATH": "/usr/bin:/bin"}
    p = subprocess.run(
        [sys.executable, "-m", "ddflow", "hooks", "session-start"],
        cwd=wt,
        capture_output=True,
        text=True,
        env=env,
        timeout=300,
    )
    assert p.returncode == 0, p.stderr
    assert "1 commit(s) behind" in p.stdout
    # The phrase, not the filename: the brief names AGENTS.md anyway ("see AGENTS.md").
    assert "rules changed there: AGENTS.md" in p.stdout

    p = subprocess.run(
        [sys.executable, "-m", "ddflow", "hooks", "session-start"],
        cwd=repo,
        capture_output=True,
        text=True,
        env=env,
        timeout=300,
    )
    assert "behind" not in p.stdout, "the primary checkout is not behind itself"


def test_the_mcp_tool_installs_it_too(repo):
    run_cli(repo, "init")
    srv = Server(repo)
    reply = srv.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "ddflow_hooks", "arguments": {"action": "install", "claude": True}},
        }
    )
    assert not reply["result"].get("isError"), reply
    assert len(_ours(_settings(repo))) == 1
