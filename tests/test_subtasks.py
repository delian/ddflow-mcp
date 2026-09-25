"""Sub-tasks, and splitting a task while it is being worked.

Mid-task discovery is the normal case: a task turns out to be two things, or working
it surfaces a requirement nobody had. A queue that cannot absorb that pushes the work
into someone's head, which is where it is lost.

A sub-task is not a separate concept here — it is a task whose parent is a task. Every
rule about globs, dependencies, leases and parallelism applies to it unchanged, which
is the whole reason it is modelled that way.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import finish, run_cli

OK, FAIL, NOTHING, REFUSED = 0, 1, 2, 3


def _finish(repo, item, model="claude-opus-5"):
    """Pass the whole configured pipeline, then complete.

    Routed through the shared helper so that when `gates.require_outcome` or the
    default pipeline changes, every test that just wants a finished item follows
    without a sweep through thirteen files.
    """
    return finish(repo, item, model=model)


def _proj(repo):
    run_cli(repo, "init")
    run_cli(repo, "phase", "add", "P1", "--title", "Core")
    run_cli(
        repo,
        "task",
        "add",
        "P1.T1",
        "--phase",
        "P1",
        "--title",
        "importer",
        "--globs",
        "src/import/*",
    )
    return repo


# -- adding work mid-flight ------------------------------------------------------------


def test_a_task_can_be_added_while_another_is_in_flight(repo):
    _proj(repo)
    run_cli(repo, "claim", "P1.T1", "--no-worktree")
    assert (
        run_cli(
            repo,
            "task",
            "add",
            "P1.T2",
            "--phase",
            "P1",
            "--title",
            "discovered requirement",
            "--globs",
            "src/new/*",
        )[0]
        == OK
    )
    ready = [
        r["id"] for r in json.loads(run_cli(repo, "--json", "next", "--phase", "P1")[1])["ready"]
    ]
    assert "P1.T2" in ready


def test_a_new_task_can_depend_on_the_one_being_worked(repo):
    _proj(repo)
    run_cli(repo, "claim", "P1.T1", "--no-worktree")
    run_cli(
        repo, "task", "add", "P1.T2", "--phase", "P1", "--needs", "P1.T1", "--globs", "src/after/*"
    )
    data = json.loads(run_cli(repo, "--json", "next", "--phase", "P1")[1])
    blocked = {b["item"]: b for b in data["blocked"]}
    assert blocked["P1.T2"]["reason"] == "deps"


# -- sub-tasks ---------------------------------------------------------------------------


def test_a_task_can_parent_another_task(repo):
    _proj(repo)
    assert (
        run_cli(
            repo,
            "task",
            "add",
            "P1.T1a",
            "--parent",
            "P1.T1",
            "--title",
            "half one",
            "--globs",
            "src/import/a.py",
        )[0]
        == OK
    )
    shown = json.loads(run_cli(repo, "--json", "show", "P1.T1a")[1])
    assert shown["parent"] == "P1.T1"


def test_sub_tasks_are_offered_by_the_scheduler(repo):
    """Filtering candidates on DIRECT parentage excluded them entirely: a task split
    into two left both halves unreachable while the queue looked empty."""
    _proj(repo)
    run_cli(repo, "task", "add", "P1.T1a", "--parent", "P1.T1", "--globs", "src/a.py")
    ready = [
        r["id"] for r in json.loads(run_cli(repo, "--json", "next", "--phase", "P1")[1])["ready"]
    ]
    assert "P1.T1a" in ready


def test_independent_sub_tasks_are_offered_together(repo):
    _proj(repo)
    run_cli(repo, "task", "add", "P1.T1a", "--parent", "P1.T1", "--globs", "src/a.py")
    run_cli(repo, "task", "add", "P1.T1b", "--parent", "P1.T1", "--globs", "src/b.py")
    ready = sorted(
        r["id"] for r in json.loads(run_cli(repo, "--json", "next", "--phase", "P1")[1])["ready"]
    )
    assert ready == ["P1.T1a", "P1.T1b"], f"sub-tasks must parallelise: {ready}"


def test_dependent_sub_tasks_serialise(repo):
    _proj(repo)
    run_cli(repo, "task", "add", "P1.T1a", "--parent", "P1.T1", "--globs", "src/a.py")
    run_cli(
        repo,
        "task",
        "add",
        "P1.T1b",
        "--parent",
        "P1.T1",
        "--globs",
        "src/b.py",
        "--needs",
        "P1.T1a",
    )
    data = json.loads(run_cli(repo, "--json", "next", "--phase", "P1")[1])
    assert [r["id"] for r in data["ready"]] == ["P1.T1a"]
    assert {b["item"]: b["reason"] for b in data["blocked"]}["P1.T1b"] == "deps"


def test_a_parent_with_open_children_is_not_offered_as_ready(repo):
    """An umbrella is not something to claim — its children are."""
    _proj(repo)
    run_cli(repo, "task", "add", "P1.T1a", "--parent", "P1.T1", "--globs", "src/a.py")
    data = json.loads(run_cli(repo, "--json", "next", "--phase", "P1")[1])
    assert "P1.T1" not in [r["id"] for r in data["ready"]]
    umbrella = {b["item"]: b for b in data["blocked"]}["P1.T1"]
    assert umbrella["reason"] == "umbrella" and "P1.T1a" in umbrella["detail"]


def test_a_parent_cannot_complete_while_a_child_is_open(repo):
    _proj(repo)
    run_cli(repo, "task", "add", "P1.T1a", "--parent", "P1.T1", "--globs", "src/a.py")
    code, _, err = _finish(repo, "P1.T1")
    assert code == REFUSED and "sub-task(s) unfinished" in err and "P1.T1a" in err


def test_a_parent_completes_once_its_children_do(repo):
    _proj(repo)
    run_cli(repo, "task", "add", "P1.T1a", "--parent", "P1.T1", "--globs", "src/a.py")
    run_cli(repo, "claim", "P1.T1a", "--no-worktree")
    assert _finish(repo, "P1.T1a")[0] == OK
    assert _finish(repo, "P1.T1")[0] == OK


def test_an_abandoned_child_does_not_hold_its_parent_open(repo):
    _proj(repo)
    run_cli(repo, "task", "add", "P1.T1a", "--parent", "P1.T1", "--globs", "src/a.py")
    run_cli(repo, "abandon", "P1.T1a", "--reason", "not needed")
    assert _finish(repo, "P1.T1")[0] == OK


def test_a_phase_counts_sub_tasks_nested_any_depth_below_it(repo):
    """Otherwise a phase closes while work three levels down is still open."""
    _proj(repo)
    run_cli(repo, "task", "add", "P1.T1a", "--parent", "P1.T1", "--globs", "src/a.py")
    run_cli(repo, "task", "add", "P1.T1a1", "--parent", "P1.T1a", "--globs", "src/a1.py")
    for g in (
        "tasks",
        "unit_tests",
        "merge",
        "research",
        "bug_hunt",
        "dedupe",
        "live_test",
        "corrections",
    ):
        run_cli(repo, "gate", "record", "P1", g, "--outcome", "passed", "--evidence", "ok")
    code, _, err = run_cli(repo, "complete", "P1", "--model", "claude-opus-5")
    assert code == REFUSED
    assert "P1.T1a1" in err, f"a grandchild did not block its phase:\n{err}"


# -- split -----------------------------------------------------------------------------


def test_split_keeps_the_original_id_and_history(repo):
    """Closing the task and opening two new ones loses the thread between the work
    that was planned and the work that happened — which is what replay needs."""
    _proj(repo)
    run_cli(repo, "claim", "P1.T1", "--no-worktree")
    code, out, _ = run_cli(
        repo, "split", "P1.T1", "--into", "P1.T1a=parse", "--into", "P1.T1b=write"
    )
    assert code == OK
    assert "keeps its id and history" in out
    shown = json.loads(run_cli(repo, "--json", "show", "P1.T1")[1])
    assert shown["state"] != "removed" and shown["title"] == "importer"
    assert "Split into" in shown["body"]
    for child in ("P1.T1a", "P1.T1b"):
        kid = json.loads(run_cli(repo, "--json", "show", child)[1])
        assert kid["parent"] == "P1.T1"


def test_split_children_inherit_the_parents_globs(repo):
    """While the split is half-done the children are the only things being worked, and
    a child with no declared globs is one the conflict detector cannot protect."""
    _proj(repo)
    run_cli(repo, "split", "P1.T1", "--into", "P1.T1a", "--into", "P1.T1b")
    kid = json.loads(run_cli(repo, "--json", "show", "P1.T1a")[1])
    assert kid["globs"] == ["src/import/*"]


def test_split_releases_the_parents_lease(repo):
    """Holding it would block the children on a glob conflict with their own parent."""
    _proj(repo)
    run_cli(repo, "claim", "P1.T1", "--no-worktree")
    run_cli(repo, "split", "P1.T1", "--into", "P1.T1a", "--into", "P1.T1b")
    assert json.loads(run_cli(repo, "--json", "show", "P1.T1")[1])["lease"] is None
    run_cli(repo, "update", "P1.T1a", "--globs", "src/import/a.py")
    assert run_cli(repo, "claim", "P1.T1a", "--no-worktree")[0] == OK


def test_splitting_into_one_piece_is_refused(repo):
    _proj(repo)
    code, _, err = run_cli(repo, "split", "P1.T1", "--into", "P1.T1a")
    assert code == FAIL and "not a split" in err


def test_splitting_finished_work_is_refused(repo):
    _proj(repo)
    run_cli(repo, "claim", "P1.T1", "--no-worktree")
    _finish(repo, "P1.T1")
    code, _, err = run_cli(repo, "split", "P1.T1", "--into", "A", "--into", "B")
    assert code == REFUSED and "already done" in err


def test_the_board_nests_sub_tasks_under_their_parent(repo):
    _proj(repo)
    run_cli(repo, "split", "P1.T1", "--into", "P1.T1a=parse", "--into", "P1.T1b=write")
    board = run_cli(repo, "board")[1]
    rows = [ln for ln in board.splitlines() if "**P1.T1" in ln]
    assert len(rows) == 3
    assert "&nbsp;" not in rows[0], "the parent must not be indented"
    assert "&nbsp;" in rows[1] and "&nbsp;" in rows[2], "children must be indented"


def test_giving_a_claimed_task_its_first_child_releases_its_lease(repo):
    """Becoming an umbrella is a transition, and it has to happen however you get there.

    `ddflow split` released the parent's lease with a comment explaining why: an
    umbrella holding a live claim on globs that overlap every child's means a SECOND
    agent cannot take one of those children, and crash recovery points at a worktree
    where nothing further will ever happen. Adding a sub-task by hand reaches the same
    state by a different route, and did not release — so the fix lived in one of the
    two paths into the condition it guards.
    """
    run_cli(repo, "init")
    run_cli(repo, "phase", "add", "P1", "--globs", "core/**")
    run_cli(repo, "task", "add", "P1.T1", "--phase", "P1", "--globs", "core/entry.py")
    assert run_cli(repo, "claim", "P1.T1", "--no-worktree", agent="alpha")[0] == OK

    code, out, err = run_cli(
        repo, "task", "add", "P1.T1.a", "--parent", "P1.T1", "--globs", "core/entry.py"
    )
    assert code == OK, err
    assert "umbrella" in out.lower(), f"and it must say what it did and why: {out}"

    shown = json.loads(run_cli(repo, "--json", "show", "P1.T1")[1])
    assert not shown["lease"], f"the umbrella must not still hold a claim: {shown['lease']}"

    # The point of releasing: a different agent can now take the child, whose globs
    # overlap the parent's.
    code, _out, err = run_cli(repo, "claim", "P1.T1.a", "--no-worktree", agent="beta")
    assert code == OK, f"a second agent must be able to work the sub-task:\n{err}"


def test_adding_a_task_to_a_claimed_PHASE_does_not_release_the_phase(repo):
    """A phase with tasks is the normal state, not a transition — nothing to release.

    Stated as its own test because the obvious over-correction is to release on every
    `task add`, which would drop an operator's deliberate phase-level claim every time
    they typed another task into the plan.
    """
    run_cli(repo, "init")
    run_cli(repo, "phase", "add", "P1", "--globs", "core/**")
    run_cli(repo, "claim", "P1", "--no-worktree", agent="alpha")
    run_cli(repo, "task", "add", "P1.T1", "--phase", "P1", "--globs", "core/a.py")
    shown = json.loads(run_cli(repo, "--json", "show", "P1")[1])
    assert shown["lease"], "a phase's claim survives its tasks being written"
