"""A phase has no branch: its merge gate resolves as not applicable, from facts.

B-phase-merge-gate: the gate used to be hand-recorded `passed` ("main holds everything")
because `gate skip merge` and `merge <phase>` were both refused. `merge` now `needs_branch`,
so a branchless PHASE reports it not applicable (an unclaimed task still owes it); a phase
additionally refuses to complete
while a finished task's branch is not on the base.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import pass_pipeline, run_cli
from helpers import git as _git


def _task_with_commit(repo: Path, tid: str, phase: str) -> Path:
    run_cli(repo, "task", "add", tid, "--title", "t", "--phase", phase)
    code, out, err = run_cli(repo, "--json", "claim", tid)
    assert code == 0, out + err
    tree = Path(json.loads(out)["worktree"])
    (tree / f"{tid}.txt").write_text("x\n")
    _git(tree, "add", ".")
    _git(tree, "commit", "-qm", f"work {tid}")
    return tree


def _finish(repo: Path, tid: str, *, merge: bool) -> None:
    if merge:
        assert run_cli(repo, "merge", tid)[0] == 0
    pass_pipeline(repo, tid, omit=() if merge else ("merge",))
    code, out, err = run_cli(
        repo, "complete", tid, "--model", "claude-opus-5", "--force", "--reason", "fixture"
    )
    assert code == 0, out + err


def test_a_phase_reports_merge_not_applicable(repo):
    run_cli(repo, "init")
    run_cli(repo, "phase", "add", "P1", "--title", "p")
    out = run_cli(repo, "gate", "status", "P1")[1]
    assert "[-] merge" in out and "no branch" in out, out
    data = json.loads(run_cli(repo, "--json", "gate", "status", "P1")[1])
    assert "merge" not in data.get("silent", []), data


def test_a_phase_with_merged_tasks_completes_without_a_hand_recorded_merge(repo):
    run_cli(repo, "init")
    # This is about the merge gate; the full-suite close check (DX-phase-acceptance-full-suite,
    # default 'require') has its own tests, so it is switched off here.
    assert run_cli(repo, "config", "schedule.phase_full_suite", "off")[0] == 0
    run_cli(repo, "phase", "add", "P1", "--title", "p")
    _task_with_commit(repo, "T1", "P1")
    _finish(repo, "T1", merge=True)
    pass_pipeline(repo, "P1", omit=("merge",))
    code, out, err = run_cli(repo, "complete", "P1", "--model", "claude-opus-5")
    assert code == 0, out + err
    runs = json.loads(run_cli(repo, "--json", "show", "P1")[1])
    assert "merge" not in json.dumps(runs.get("gates", {})), runs


def test_a_phase_with_an_unmerged_task_branch_refuses(repo):
    run_cli(repo, "init")
    run_cli(repo, "phase", "add", "P1", "--title", "p")
    _task_with_commit(repo, "T1", "P1")
    _finish(repo, "T1", merge=False)
    pass_pipeline(repo, "P1", omit=("merge",))
    code, out, err = run_cli(repo, "complete", "P1", "--model", "claude-opus-5")
    assert code == 3, out + err
    assert "not merged" in out + err and "T1" in out + err, out + err


def test_an_ordinary_task_still_needs_merge(repo):
    run_cli(repo, "init")
    _task_with_commit(repo, "T1", "")
    assert "[ ] merge" in run_cli(repo, "gate", "status", "T1")[1]
    pass_pipeline(repo, "T1", omit=("merge",))
    code, out, err = run_cli(repo, "complete", "T1", "--model", "claude-opus-5")
    assert code == 3 and "merge" in out + err, out + err


def test_an_unclaimed_task_still_owes_merge(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "t")
    assert "[ ] merge" in run_cli(repo, "gate", "status", "T1")[1]


def test_a_task_base_that_no_longer_exists_does_not_hide_unmerged_work(repo):
    """The backstop judges against the phase's base when the task's recorded base branch
    has since been deleted, rather than calling the work merged."""
    from ddflow.config import Config
    from ddflow.core.model import Item, State
    from ddflow.services.completion import _unmerged_branches

    _git(repo, "checkout", "-q", "-b", "t/T")
    (repo / "x.txt").write_text("x\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "work")
    _git(repo, "checkout", "-q", "main")
    st = State()
    phase = Item(id="P", kind="phase", title="p")
    task = Item(id="T", kind="task", title="t", branch="t/T", base="gone/base", state="done")
    task.parent = "P"
    st.items = {"P": phase, "T": task}
    assert _unmerged_branches(st, Config(), phase, repo) != []
