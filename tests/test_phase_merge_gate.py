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


def _finish(repo: Path, tid: str) -> None:
    assert run_cli(repo, "merge", tid)[0] == 0
    pass_pipeline(repo, tid, omit=())
    code, out, err = run_cli(repo, "complete", tid, "--model", "claude-opus-5")
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
    _finish(repo, "T1")
    pass_pipeline(repo, "P1", omit=("merge",))
    code, out, err = run_cli(repo, "complete", "P1", "--model", "claude-opus-5")
    assert code == 0, out + err
    runs = json.loads(run_cli(repo, "--json", "show", "P1")[1])
    assert "merge" not in json.dumps(runs.get("gates", {})), runs


def test_a_phase_with_an_unmerged_task_branch_refuses(repo):
    """A finished sub-task whose branch never reached the base still refuses the phase.
    Built from the facts: completing that sub-task now refuses on the same unmerged branch
    (Bd59dded6a4), so the done-but-unmerged state is not reachable through `complete` and
    the phase backstop is judged straight from the tree."""
    from ddflow.config import Config
    from ddflow.core.model import Item, State
    from ddflow.services.completion import _unmerged_branches

    run_cli(repo, "init")
    run_cli(repo, "phase", "add", "P1", "--title", "p")
    _git(repo, "checkout", "-q", "-b", "ddflow/T1")
    (repo / "T1.txt").write_text("x\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "work T1")
    _git(repo, "checkout", "-q", "main")
    st = State()
    phase = Item(id="P1", kind="phase", title="p")
    task = Item(id="T1", kind="task", title="t", branch="ddflow/T1", base="main", state="done")
    task.parent = "P1"
    st.items = {"P1": phase, "T1": task}
    assert _unmerged_branches(st, Config(), phase, repo) != []


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


def test_a_task_with_unmerged_commits_refuses_force(repo):
    """Bd59dded6a4: complete --force on a released item whose branch still held unmerged
    commits recorded it done with those commits dropped. The task's own branch must refuse
    even under --force, because force overrides the merge GATE, not the fact that the work
    never reached the base."""
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", "a.py")
    code, out, err = run_cli(repo, "--json", "claim", "T1")
    assert code == 0, out + err
    wt = Path(json.loads(out)["worktree"])
    (wt / "a.py").write_text("x = 1\n")
    _git(wt, "add", ".")
    _git(wt, "commit", "-qm", "work T1")
    pass_pipeline(repo, "T1", omit=("merge",))
    # Bd59dded6a4: `release` left the item claimable and completable by anyone with the
    # branch still unmerged. Release drops the lease, not the branch, so the guard must
    # still refuse a force-complete of the released item.
    assert run_cli(repo, "release", "T1")[0] == 0
    code, out, err = run_cli(repo, "complete", "T1", "--model", "claude-opus-5", "--force")
    assert code == 3, out + err
    assert "not merged" in out + err and "T1" in out + err, out + err


def test_a_task_with_a_partial_merge_refuses_force(repo):
    """Bd59dded6a4 was released with a PARTIAL merge: the base held the branch's first
    commit and the follow-ups were dropped. Forwarding the base onto the first commit itself
    (not a `cherry-pick`, which would forge a new sha) leaves only the later commit
    unmerged, so the guard must still refuse a force-complete of the released item."""
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", "a.py")
    code, out, err = run_cli(repo, "--json", "claim", "T1")
    assert code == 0, out + err
    wt = Path(json.loads(out)["worktree"])
    (wt / "a.py").write_text("x = 1\n")
    _git(wt, "add", ".")
    _git(wt, "commit", "-qm", "work T1 part 1")
    _git(repo, "merge", "--ff-only", "ddflow/T1")  # the base reaches part 1 itself
    (wt / "a.py").write_text("x = 2\n")
    _git(wt, "add", ".")
    _git(wt, "commit", "-qm", "work T1 part 2")
    pass_pipeline(repo, "T1", omit=("merge",))
    assert run_cli(repo, "release", "T1")[0] == 0
    code, out, err = run_cli(repo, "complete", "T1", "--model", "claude-opus-5", "--force")
    assert code == 3, out + err
    assert "not merged" in out + err and "T1" in out + err, out + err


def test_a_task_that_merged_completes_after_the_guard(repo):
    """The guard must not fire on the normal flow: once `merge` lands the branch, the same
    --force is not even needed, but the branch is merged and no unmerged-commit blocker
    remains."""
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", "a.py")
    code, out, err = run_cli(repo, "--json", "claim", "T1")
    assert code == 0, out + err
    wt = Path(json.loads(out)["worktree"])
    (wt / "a.py").write_text("x = 1\n")
    _git(wt, "add", ".")
    _git(wt, "commit", "-qm", "work T1")
    run_cli(repo, "merge", "T1")
    pass_pipeline(repo, "T1", omit=())
    code, out, err = run_cli(repo, "complete", "T1", "--model", "claude-opus-5")
    assert code == 0, out + err


def test_a_commit_after_a_recorded_landing_is_still_refused(repo):
    """A branch at the tip `merge` landed is merged, even with no ancestry (a squash); one
    commit past it is not, so the exemption cannot hide later work."""
    from ddflow.config import Config
    from ddflow.core.model import Item, State
    from ddflow.services.completion.rules import UNMERGED_BLOCKER, _own_branch_unmerged

    _git(repo, "branch", "feature")
    tip = _git(repo, "rev-parse", "feature").strip()
    _git(repo, "checkout", "-q", "feature")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "landed by squash")
    landed = _git(repo, "rev-parse", "HEAD").strip()
    _git(repo, "checkout", "-q", "main")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "the squash commit")
    squash = _git(repo, "rev-parse", "HEAD").strip()
    it = Item(
        id="T1", kind="task", branch="feature", base="main", merged_head=landed, merged_sha=squash
    )
    assert tip != landed
    assert _own_branch_unmerged(State(), Config(), it, repo) == []
    _git(repo, "reset", "-q", "--hard", "HEAD~1")  # the base rewound past the landing
    assert UNMERGED_BLOCKER in _own_branch_unmerged(State(), Config(), it, repo)[0]
    _git(repo, "reset", "-q", "--hard", squash)
    _git(repo, "checkout", "-q", "feature")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "after the landing")
    _git(repo, "checkout", "-q", "main")
    assert UNMERGED_BLOCKER in _own_branch_unmerged(State(), Config(), it, repo)[0]
