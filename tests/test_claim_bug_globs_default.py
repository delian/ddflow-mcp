"""A fix task's bug globs are a DEFAULT, never a reason to wait (B-claim-fix-task-takes-bug-globs).

Claiming `fix-<bug>` only to close it waited on another agent's lease of a file the bug
named. The bug's files that another live lease holds are left off the claim, and the
answer says which files came from the bug and which were left off.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from helpers import state_as_reader as state

from ddflow.api import lifecycle as LC
from ddflow.infra.log import EventLog


def _seed(repo: Path, bug_globs: list[str]) -> None:
    run_cli(repo, "init")
    log = EventLog(repo, "seed")
    log.append("phase.added", "P1", {"title": "p"})
    log.append("task.added", "T2", {"parent": "P1", "title": "t2", "globs": ["src/h.py"]})
    log.append("bug.found", "Bh", {"summary": "s", "globs": bug_globs})
    log.append(
        "task.added", "fix-Bh", {"parent": "P1", "title": "fix", "fixes": ["Bh"], "globs": []}
    )
    other = EventLog(repo, "other")
    other.append(
        "lease.acquired",
        "T2",
        {"holder": "other", "at": time.time(), "ttl_s": 1800, "globs": ["src/h.py"]},
    )
    other.append("item.started", "T2", {})


def test_bug_globs_another_lease_holds_are_left_off_not_waited_on(repo):
    _seed(repo, ["src/h.py", "src/free.py"])
    out = LC.claim(repo, "fix-Bh", agent="w", no_worktree=True)
    assert out.exit == 0, out.reason
    assert out.data["globs"] == ["src/free.py"]
    assert out.data["globs_from_bug"] == {
        "bugs": ["Bh"],
        "kept": ["src/free.py"],
        "left_off": ["src/h.py"],
    }
    assert state(repo).items["fix-Bh"].lease.globs == ["src/free.py"]


def test_every_bug_glob_held_elsewhere_still_claims_with_none(repo):
    _seed(repo, ["src/h.py"])
    out = LC.claim(repo, "fix-Bh", agent="w", no_worktree=True)
    assert out.exit == 0, out.reason
    assert out.data["globs"] == []
    assert out.data["globs_from_bug"]["left_off"] == ["src/h.py"]


def test_the_cli_says_the_globs_came_from_the_bug(repo):
    _seed(repo, ["src/h.py", "src/free.py"])
    code, out, err = run_cli(repo, "claim", "fix-Bh", "--no-worktree", agent="w")
    assert code == 0, out + err
    assert "from bug Bh" in out and "left off" in out and "src/h.py" in out, out


def test_free_bug_globs_are_taken_whole_and_say_so(repo):
    _seed(repo, ["src/free.py"])
    out = LC.claim(repo, "fix-Bh", agent="w", no_worktree=True)
    assert out.data["globs"] == ["src/free.py"]
    assert out.data["globs_from_bug"] == {"bugs": ["Bh"], "kept": ["src/free.py"], "left_off": []}


def test_a_claim_naming_its_own_globs_has_no_bug_note(repo):
    _seed(repo, ["src/free.py"])
    out = LC.claim(repo, "fix-Bh", agent="w", globs="src/mine.py", no_worktree=True)
    assert out.exit == 0, out.reason
    assert "globs_from_bug" not in out.data


def test_a_bug_that_declared_no_files_adds_no_note(repo):
    _seed(repo, [])
    out = LC.claim(repo, "fix-Bh", agent="w", no_worktree=True)
    assert out.exit == 0, out.reason
    assert out.data["globs"] == [] and "globs_from_bug" not in out.data
