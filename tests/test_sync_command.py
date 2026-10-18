"""DX-sync-command: `ddflow sync` merges the recorded base into the item's branch when safe."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from conftest import run_cli

from ddflow.api import lifecycle as A
from ddflow.api.lifecycle.sync import sync
from ddflow.infra import worktree as W

ME = "agent-sync"


def _git(cwd, *a):
    return subprocess.run(
        ["git", "-C", str(cwd), *a], capture_output=True, text=True, check=True
    ).stdout.strip()


def _land(repo: Path, name: str, text: str = "x\n") -> None:
    (repo / name).parent.mkdir(parents=True, exist_ok=True)
    (repo / name).write_text(text)
    _git(repo, "add", name)
    _git(repo, "commit", "-qm", f"main: {name}")


def _claimed(repo: Path, globs: str = "src/"):
    run_cli(repo, "init")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init ddflow")
    run_cli(repo, "task", "add", "T1", "--globs", globs)
    out = A.claim(repo, "T1", agent=ME)
    assert out.ok, out.reason
    return W.load_path(repo, _items(repo)["T1"].worktree)


def _items(repo):
    from helpers import items_of

    return items_of(repo)


def test_up_to_date_is_nothing_to_do(repo):
    _claimed(repo)
    out = sync(repo, item="T1", agent=ME)
    assert out.data["status"] == "current" and not out.ok


def test_check_measures_and_counts_commits_touching_the_globs(repo):
    wt = _claimed(repo)
    _land(repo, "src/a.py")
    _land(repo, "docs/b.md")
    head = _git(wt, "rev-parse", "HEAD")
    out = sync(repo, item="T1", check=True, agent=ME)
    assert out.data["status"] == "behind" and out.data["behind"] == 2
    assert out.data["touching_globs"] == 1
    assert _git(wt, "rev-parse", "HEAD") == head


def test_sync_merges_the_base_and_reports_gate_validity(repo):
    wt = _claimed(repo)
    _land(repo, "src/a.py")
    out = sync(repo, item="T1", agent=ME)
    assert out.ok, out.reason
    assert out.data["status"] == "merged" and (wt / "src" / "a.py").exists()
    assert "gates_valid" in out.data and "gates_stale" in out.data


def test_uncommitted_changes_refuse_the_sync(repo):
    wt = _claimed(repo)
    _land(repo, "src/a.py")
    (wt / "README.md").write_text("dirty\n")
    head = _git(wt, "rev-parse", "HEAD")
    out = sync(repo, item="T1", agent=ME)
    assert out.exit != 0 and out.data["status"] == "refused" and "README.md" in out.data["dirty"]
    assert _git(wt, "rev-parse", "HEAD") == head


def test_a_conflict_aborts_the_merge_and_names_the_files(repo):
    wt = _claimed(repo)
    _land(repo, "src/c.py", "main\n")
    (wt / "src").mkdir(exist_ok=True)
    (wt / "src" / "c.py").write_text("branch\n")
    _git(wt, "add", "src/c.py")
    _git(wt, "commit", "-qm", "branch c")
    head = _git(wt, "rev-parse", "HEAD")
    out = sync(repo, item="T1", agent=ME)
    assert out.data["status"] == "conflict" and "src/c.py" in out.data["conflicts"]
    assert _git(wt, "rev-parse", "HEAD") == head
    assert _git(wt, "status", "--porcelain") == ""
    assert (
        subprocess.run(
            ["git", "-C", str(wt), "rev-parse", "-q", "--verify", "MERGE_HEAD"], capture_output=True
        ).returncode
        != 0
    )


def test_the_command_is_declared_and_runs_from_the_cli(repo):
    _claimed(repo)
    code, out, err = run_cli(repo, "sync", "T1", "--check")
    assert code == 2, err
    assert "up to date" in (out + err)


def test_an_untracked_file_the_base_adds_is_a_refusal_not_a_conflict(repo):
    wt = _claimed(repo)
    _land(repo, "src/new.py", "base\n")
    (wt / "src").mkdir(exist_ok=True)
    (wt / "src" / "new.py").write_text("mine\n")
    out = sync(repo, item="T1", agent=ME)
    assert out.data["status"] == "refused" and not out.data["conflicts"]
    assert (wt / "src" / "new.py").read_text() == "mine\n"
