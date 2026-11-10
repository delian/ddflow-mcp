"""DX-sync-event: `ddflow sync` records a `branch.synced` event when it merges the base."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from conftest import run_cli

from ddflow.api import lifecycle as A
from ddflow.infra import worktree as W

sync = A.sync

ME = "agent-sync"


def _git(cwd, *a):
    return subprocess.run(
        ["git", "-C", str(cwd), *a], capture_output=True, text=True, check=True
    ).stdout.strip()


def _land(repo: Path, name: str, text: str = "x\n") -> str:
    (repo / name).parent.mkdir(parents=True, exist_ok=True)
    (repo / name).write_text(text)
    _git(repo, "add", name)
    _git(repo, "commit", "-qm", f"main: {name}")
    return _git(repo, "rev-parse", "HEAD")


def _claimed(repo: Path):
    run_cli(repo, "init")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init ddflow")
    run_cli(repo, "task", "add", "T1", "--title", "T1 fixture", "--globs", "src/")
    out = A.claim(repo, "T1", agent=ME)
    assert out.ok, out.reason
    return W.load_path(repo, _items(repo)["T1"].worktree)


def _items(repo):
    from helpers import items_of

    return items_of(repo)


def _events(repo):
    from ddflow.infra.log import EventLog

    return EventLog(repo, "reader").read_all()


def test_sync_records_a_branch_synced_event_with_the_merge_and_gate_outcomes(repo):
    wt = _claimed(repo)
    (wt / "src").mkdir(exist_ok=True)
    (wt / "src" / "mine.py").write_text("x = 1\n")
    _git(wt, "add", "src/mine.py")
    _git(wt, "commit", "-qm", "mine")
    r = run_cli(wt, "gate", "record", "T1", "standards", "--outcome", "passed", "--evidence", "ran",
                "--agent", ME)
    assert r[0] == 0, r
    c1 = _land(repo, "docs/b.md")  # outside the globs, so the judged gate stays valid
    out = sync(repo, item="T1", agent=ME)
    assert out.ok, out.reason
    (last,) = [e for e in _events(repo) if e.kind == "branch.synced"][-1:]
    assert last.subject == "T1"
    assert last.data["base_sha"] == c1
    assert last.data["commits"] == [c1]
    assert "standards" in last.data["gates_valid"]
    assert last.data["gates_stale"] == []


def test_sync_records_applied_commits_touching_the_globs_and_the_relevant_count(repo):
    wt = _claimed(repo)
    c1 = _land(repo, "src/a.py")
    c2 = _land(repo, "docs/b.md")
    out = sync(repo, item="T1", agent=ME)
    assert out.ok, out.reason
    (last,) = [e for e in _events(repo) if e.kind == "branch.synced"][-1:]
    # commits are newest first; only src/a.py touches the globs, so relevant == 1
    assert last.data["commits"] == [c2, c1]
    assert last.data["relevant"] == 1


def test_a_current_sync_records_no_event(repo):
    _claimed(repo)
    out = sync(repo, item="T1", agent=ME)
    assert out.data["status"] == "current"
    assert not [e for e in _events(repo) if e.kind == "branch.synced"]
