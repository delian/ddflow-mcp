"""`ddflow update` can opt a fix task out of its bug's globs, move a task to another phase,
and change a task's kind (B5df7832d0a, B-update-cannot-reparent-task,
B-doc-task-kind-not-settable-after-add).

* `--globs ""` on a fix task says "cover nothing from the bug": a claim then takes no bug
  globs, where an item that never had globs declared still gets them as a default.
* `--phase` / `--parent` re-parent a task, refused when the new parent is not a live item,
  is the task or one of its own descendants, or would make a dependency cycle through the
  phase's inherited needs.
* `--kind` moves a task to another work kind (and so another gate pipeline).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from helpers import state_as_reader as state

from ddflow.api import lifecycle as LC
from ddflow.infra.log import EventLog
from ddflow.surfaces.mcp import Server


def _seed(repo: Path) -> EventLog:
    run_cli(repo, "init")
    log = EventLog(repo, "seed")
    log.append("phase.added", "P1", {"title": "one"})
    log.append("phase.added", "P2", {"title": "two"})
    log.append("task.added", "T1", {"parent": "P1", "title": "t1", "globs": ["src/a.py"]})
    log.append("bug.found", "Bh", {"summary": "s", "globs": ["src/h.py"]})
    log.append(
        "task.added", "fix-Bh", {"parent": "P1", "title": "fix", "fixes": ["Bh"], "globs": []}
    )
    return log


def _mcp(repo: Path, args: dict) -> dict:
    r = Server(repo).handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "ddflow_update", "arguments": args},
        }
    )["result"]
    return {"error": r.get("isError", False), "text": r["content"][0]["text"]}


# -- B5df7832d0a: the opt-out ----------------------------------------------------------


def test_globs_empty_opts_a_fix_task_out_of_its_bugs_globs(repo):
    _seed(repo)
    code, out, err = run_cli(repo, "update", "fix-Bh", "--globs", "")
    assert code == 0, out + err
    claimed = LC.claim(repo, "fix-Bh", agent="w", no_worktree=True)
    assert claimed.exit == 0, claimed.reason
    assert claimed.data["globs"] == [], claimed.data
    assert "globs_from_bug" not in claimed.data
    assert state(repo).items["fix-Bh"].lease.globs == []


def test_an_untouched_fix_task_still_takes_the_bugs_globs(repo):
    _seed(repo)
    claimed = LC.claim(repo, "fix-Bh", agent="w", no_worktree=True)
    assert claimed.data["globs"] == ["src/h.py"]


def test_naming_globs_again_takes_the_opt_out_back(repo):
    _seed(repo)
    assert run_cli(repo, "update", "fix-Bh", "--globs", "")[0] == 0
    assert run_cli(repo, "update", "fix-Bh", "--globs", "src/x.py")[0] == 0
    assert state(repo).items["fix-Bh"].globs == ["src/x.py"]
    assert LC.claim(repo, "fix-Bh", agent="w", no_worktree=True).data["globs"] == ["src/x.py"]
    assert LC.release(repo, "fix-Bh", agent="w").exit == 0
    assert run_cli(repo, "update", "fix-Bh", "--globs", "")[0] == 0
    assert LC.claim(repo, "fix-Bh", agent="w", no_worktree=True).data["globs"] == []


# -- B-update-cannot-reparent-task ------------------------------------------------------


def test_update_phase_moves_a_task_and_records_it(repo):
    _seed(repo)
    code, out, err = run_cli(repo, "update", "T1", "--phase", "P2")
    assert code == 0, out + err
    assert state(repo).items["T1"].parent == "P2"
    assert "parent" in out


def test_update_parent_is_the_same_field_and_may_be_a_task(repo):
    _seed(repo)
    EventLog(repo, "seed").append("task.added", "T2", {"parent": "P1", "title": "t2"})
    assert run_cli(repo, "update", "T2", "--parent", "T1")[0] == 0
    assert state(repo).items["T2"].parent == "T1"


def test_update_phase_empty_makes_a_task_top_level(repo):
    _seed(repo)
    assert run_cli(repo, "update", "T1", "--phase", "")[0] == 0
    assert state(repo).items["T1"].parent == ""


def test_a_missing_parent_is_refused(repo):
    _seed(repo)
    code, _, err = run_cli(repo, "update", "T1", "--phase", "P9")
    assert code != 0 and "P9" in err
    assert state(repo).items["T1"].parent == "P1"


def test_a_task_cannot_be_its_own_parent_or_sit_under_its_descendant(repo):
    log = _seed(repo)
    log.append("task.added", "T2", {"parent": "T1", "title": "child"})
    assert run_cli(repo, "update", "T1", "--parent", "T1")[0] != 0
    code, _, err = run_cli(repo, "update", "T1", "--parent", "T2")
    assert code != 0 and "T2" in err
    assert state(repo).items["T1"].parent == "P1"


def test_a_move_that_closes_a_dependency_cycle_is_refused(repo):
    """P2 needs X and X needs T3: T3 under P2 would inherit its need of X."""
    log = _seed(repo)
    log.append("task.added", "T3", {"parent": "P1", "title": "t3"})
    log.append("task.added", "X", {"parent": "P1", "title": "x", "needs": ["T3"]})
    log.append("phase.updated", "P2", {"needs": ["X"]})
    code, _, err = run_cli(repo, "update", "T3", "--phase", "P2")
    assert code != 0 and "cycle" in err
    assert state(repo).items["T3"].parent == "P1"


def test_a_finished_task_is_not_moved(repo):
    log = _seed(repo)
    log.append("item.completed", "T1", {"sha": "abc"})
    code, _, err = run_cli(repo, "update", "T1", "--phase", "P2")
    assert code != 0 and "done" in err
    assert state(repo).items["T1"].parent == "P1"


def test_the_tool_moves_a_task_too(repo):
    _seed(repo)
    got = _mcp(repo, {"id": "T1", "parent": "P2"})
    assert not got["error"], got
    assert state(repo).items["T1"].parent == "P2"


# -- B-doc-task-kind-not-settable-after-add ---------------------------------------------


def test_update_kind_changes_a_tasks_kind(repo):
    _seed(repo)
    code, out, err = run_cli(repo, "update", "T1", "--kind", "doc")
    assert code == 0, out + err
    assert state(repo).items["T1"].kind == "doc"
    assert "kind" in out


def test_an_unknown_kind_or_a_phase_is_refused(repo):
    _seed(repo)
    code, _, err = run_cli(repo, "update", "T1", "--kind", "bogus")
    assert code != 0 and "bogus" in err
    code, _, err = run_cli(repo, "update", "P1", "--kind", "doc")
    assert code != 0 and "phase" in err
    assert state(repo).items["T1"].kind == "task"


def test_the_tool_sets_kind(repo):
    _seed(repo)
    got = _mcp(repo, {"id": "T1", "kind": "research"})
    assert not got["error"], got
    assert state(repo).items["T1"].kind == "research"
    assert json.loads(got["text"])["changed"] == ["kind"]


def test_batch_does_not_take_parent_or_kind(repo):
    _seed(repo)
    for entry in ({"id": "T1", "parent": "P2"}, {"id": "T1", "kind": "doc"}):
        code, _, err = run_cli(repo, "batch", "--edits", json.dumps([entry]))
        assert code != 0, err
    assert state(repo).items["T1"].parent == "P1" and state(repo).items["T1"].kind == "task"


def test_a_move_to_a_phase_on_another_line_is_refused_for_a_forked_task(repo):
    log = _seed(repo)
    log.append("phase.updated", "P2", {"line": "v2"})
    log.append("worktree.created", "T1", {"path": "/x", "branch": "b", "base": "main"})
    code, _, err = run_cli(repo, "update", "T1", "--phase", "P2")
    assert code == 3 and "release line" in err
    assert state(repo).items["T1"].parent == "P1"


def test_making_a_forked_task_top_level_cannot_change_its_line_either(repo):
    log = _seed(repo)
    log.append("phase.updated", "P1", {"line": "v2"})
    log.append("worktree.created", "T1", {"path": "/x", "branch": "b", "base": "main"})
    code, _, err = run_cli(repo, "update", "T1", "--phase", "")
    assert code != 0 and "release line" in err
    assert state(repo).items["T1"].parent == "P1"


def test_a_move_and_a_needs_edit_together_are_checked_as_one(repo):
    log = _seed(repo)
    log.append("task.added", "X", {"parent": "P1", "title": "x", "needs": ["T1"]})
    log.append("task.added", "T2", {"parent": "P1", "title": "t2"})
    code, _, err = run_cli(repo, "update", "T2", "--parent", "P2", "--needs", "X")
    assert code == 0, err  # no cycle: P2 waits on nothing
    log.append("phase.updated", "P2", {"needs": ["X"]})
    log.append("task.added", "T3", {"parent": "P1", "title": "t3"})
    log.append("task.updated", "X", {"needs": ["T3"]})
    code, _, err = run_cli(repo, "update", "T3", "--parent", "P2", "--needs", "T1")
    assert code != 0 and "cycle" in err
