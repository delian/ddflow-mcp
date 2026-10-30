"""The lane worktree's lease-checking git pre-commit hook (SW-git-fallback)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ddflow.services.swarm import githook as GH


def sh(cwd, *a, check=True):
    return subprocess.run(a, cwd=cwd, capture_output=True, text=True, check=check)


@pytest.fixture
def lane(tmp_path):
    main = tmp_path / "main"
    main.mkdir()
    sh(main, "git", "init", "-q", "-b", "main")
    sh(main, "git", "config", "user.email", "a@b.c")
    sh(main, "git", "config", "user.name", "a")
    (main / "a.txt").write_text("a")
    sh(main, "git", "add", ".")
    sh(main, "git", "commit", "-qm", "init")
    wt = tmp_path / "lane"
    sh(main, "git", "worktree", "add", "-q", "-b", "lane", str(wt))
    return main, wt


def commit(wt, name="x.txt"):
    (wt / name).write_text("x")
    sh(wt, "git", "add", name)
    return sh(wt, "git", "commit", "-qm", "c", check=False)


def stub(tmp_path, body):
    f = tmp_path / "stub.sh"
    f.write_text(f"#!/bin/sh\n{body}\n")
    f.chmod(0o755)
    return f"{f}"


def test_install_is_lane_private_and_removable(lane):
    main, wt = lane
    GH.install(wt)
    assert GH.installed(wt)
    d = GH.hook_dir(wt)
    assert d.is_dir() and "worktrees" in str(d)
    # the primary checkout's hooks setting is untouched, and no tracked file was written
    assert sh(main, "git", "config", "--get", "core.hooksPath", check=False).stdout == ""
    assert sh(wt, "git", "status", "--porcelain").stdout == ""
    GH.install(wt)  # idempotent
    assert "removed" in GH.uninstall(wt)
    assert not GH.installed(wt) and not d.exists()
    assert GH.uninstall(wt) == "no lane hook installed"


def test_refusal_message_passes_through_and_blocks(lane, tmp_path):
    _, wt = lane
    GH.install(wt, invocation=stub(tmp_path, "echo 'outside the lease: x.txt'; exit 1"))
    r = commit(wt)
    assert r.returncode != 0 and "outside the lease: x.txt" in r.stderr


def test_allowed_commit_goes_through(lane, tmp_path):
    _, wt = lane
    GH.install(wt, invocation=stub(tmp_path, "exit 0"))
    assert commit(wt).returncode == 0


def test_traceback_and_unreadable_lease_fail_open_without_leaking(lane, tmp_path):
    _, wt = lane
    GH.install(wt, invocation=stub(tmp_path, "echo 'Traceback: token=SECRET123'; exit 1"))
    r = commit(wt)
    assert r.returncode == 0 and "SECRET123" not in r.stderr and "not checked" in r.stderr
    GH.install(wt, invocation=stub(tmp_path, "echo boom SECRET123; exit 127"))
    r = commit(wt, "y.txt")
    assert r.returncode == 0 and "SECRET123" not in r.stderr and "exit 127" in r.stderr


def test_policy_and_agent_are_exported_to_the_check(lane, tmp_path):
    _, wt = lane
    out = tmp_path / "env.txt"
    GH.install(
        wt,
        agent="lane-1",
        invocation=stub(
            tmp_path, f'echo "$DDFLOW_ENFORCE_COMMIT_WITHOUT_LEASE $DDFLOW_AGENT" > {out}'
        ),
    )
    commit(wt)
    assert out.read_text().strip() == "block lane-1"


def test_the_projects_own_hooks_still_run(lane, tmp_path):
    main, wt = lane
    hooks = main / ".git" / "hooks"
    (hooks / "pre-commit").write_text("#!/bin/sh\necho PROJECT-HOOK >&2\nexit 1\n")
    (hooks / "pre-commit").chmod(0o755)
    GH.install(wt, invocation=stub(tmp_path, "exit 0"))
    r = commit(wt)
    assert r.returncode != 0 and "PROJECT-HOOK" in r.stderr
    (hooks / "commit-msg").write_text("#!/bin/sh\necho MSG-HOOK >&2\nexit 1\n")
    (hooks / "commit-msg").chmod(0o755)
    GH.install(wt, invocation=stub(tmp_path, "exit 0"))
    (hooks / "pre-commit").unlink()
    r = commit(wt, "z.txt")
    assert r.returncode != 0 and "MSG-HOOK" in r.stderr


def test_the_script_says_it_is_detection_and_uses_the_one_matcher():
    text = GH.render()
    assert "Detection at\n# commit, not prevention" in text
    assert "hooks check-commit" in text
    assert "fnmatch" not in Path(GH.__file__).read_text()
