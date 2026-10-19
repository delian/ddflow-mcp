"""A fix task's claim covers the files its bug declared (DX-claim-agent-flag-adoption).

`bug found --globs` reached the fix task only when the task was filed in the same call; a
fix task refiled by `bug file-tasks`, or one filed by hand as `fix-<bug>`, came back with
no globs and the commit hook refused until `ddflow update --globs`.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from helpers import state_as_reader as state

from ddflow.api import knowledge as K
from ddflow.infra.log import EventLog


def _seed(repo: Path) -> EventLog:
    run_cli(repo, "init")
    for f in ["src/p.py"]:
        (repo / f).parent.mkdir(parents=True, exist_ok=True)
        (repo / f).write_text("")
    log = EventLog(repo, "seed")
    log.append("phase.added", "P1", {"title": "Phase one"})
    return log


def test_the_bugs_globs_are_recorded_and_refile_carries_them(repo):
    log = _seed(repo)
    K.bug_found(repo, summary="parser drops a line", id="Bx", globs="src/p.py", agent="a")
    assert state(repo).bugs["Bx"].globs == ["src/p.py"]
    log.append("item.abandoned", "fix-Bx", {"reason": "superseded"})
    out = K.bug_file_tasks(repo, agent="a")
    task = out.data["tasks"]["Bx"]
    assert state(repo).items[task].globs == ["src/p.py"], out.data


def test_claiming_a_hand_filed_fix_task_carries_its_bugs_globs(repo):
    log = _seed(repo)
    log.append("bug.found", "Bh", {"summary": "s", "globs": ["src/h.py"]})
    log.append(
        "task.added", "fix-Bh", {"parent": "P1", "title": "fix", "fixes": ["Bh"], "globs": []}
    )
    code, out, err = run_cli(repo, "claim", "fix-Bh", agent="w")
    assert code == 0, out + err
    assert "globs:    src/h.py" in out, out
    assert state(repo).items["fix-Bh"].lease.globs == ["src/h.py"]


def test_a_claim_with_no_globs_says_the_command_that_adds_them(repo):
    log = _seed(repo)
    log.append("task.added", "T1", {"parent": "P1", "title": "t", "globs": []})
    code, out, err = run_cli(repo, "claim", "T1", agent="w")
    assert code == 0, out + err
    assert "ddflow --agent w update T1 --globs" in out, out
