"""Dependencies are INHERITED down the hierarchy.

The operator's first requirement was "dependencies between tasks *and* phases". That
sentence has a consequence that is easy to implement halfway: a phase dependency must
govern the phase's **tasks**, because a phase is never claimed — only its tasks are.
A readiness rule that reads the item's own ``needs`` and stops there makes the whole
phase-level graph decorative: `P2 needs P1` blocks P2, which nobody was going to claim,
and permits every task inside P2, which is what an agent actually picks up.

The same bug wore a second face once sub-tasks arrived: an umbrella that declares
`needs A, B` has children whose own ``needs`` are empty, so the children were offered
while A and B were still open — the scheduler handing out `entry.py` before `money.py`
existed.

Both were found by the full-lifecycle scenario, and both have one cause and one fix:
readiness consults the item's ancestors, not just the item.

The trap that hid this for so long is worth naming: the pre-existing end-to-end scenario
DID assert "P2's task is withheld while P1 is open" and it passed — because that
scenario declared `needs="P1"` on the task by hand as well as on the phase. The
assertion was true for a reason that had nothing to do with the phase graph. A test
that restates the thing under test as its own input proves nothing; these tests
deliberately never re-declare an inherited dependency.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import finish, run_cli

OK, FAIL, NOTHING, REFUSED = 0, 1, 2, 3


def _plan(repo, *argv):
    code, out, err = run_cli(repo, "--json", "next", *argv)
    assert code in (OK, NOTHING), f"exit {code}\n{out}\n{err}"
    return json.loads(out)


def _ready(plan):
    return [r["id"] for r in plan["ready"]]


def _blocked(plan):
    return {b["item"]: b for b in plan["blocked"]}


def _two_phase_project(repo):
    """P2 needs P1. Neither task re-declares that dependency — the point of the test."""
    run_cli(repo, "init")
    run_cli(repo, "phase", "add", "P1", "--title", "core", "--globs", "core/**")
    run_cli(repo, "phase", "add", "P2", "--title", "api", "--needs", "P1", "--globs", "api/**")
    run_cli(repo, "task", "add", "P1.T1", "--phase", "P1", "--globs", "core/money.py")
    run_cli(repo, "task", "add", "P2.T1", "--phase", "P2", "--globs", "api/routes.py")


# -- a phase's dependency governs the tasks inside it ---------------------------------


def test_a_task_does_not_start_while_its_phase_is_blocked(repo):
    _two_phase_project(repo)
    plan = _plan(repo)
    assert "P1.T1" in _ready(plan), "P1 has no dependencies; its task is startable"
    assert "P2.T1" not in _ready(plan), (
        "P2 needs P1 and P1 is wide open, so nothing inside P2 may start. "
        "Offering P2.T1 makes the phase graph decorative: the only item it blocks "
        f"is the one nobody claims. ready={_ready(plan)}"
    )


def test_the_refusal_names_the_phase_and_what_it_waits_on(repo):
    _two_phase_project(repo)
    b = _blocked(_plan(repo))["P2.T1"]
    assert b["reason"] == "deps", b
    assert "P1" in b["waiting_on"], f"must name the unmet dependency itself: {b}"
    assert "P2" in b["detail"], (
        f"an inherited block must say WHERE it came from, or the operator reads "
        f"'P2.T1 needs P1' and goes looking for a declaration that is not there: {b}"
    )


def test_claiming_a_task_in_a_blocked_phase_is_refused(repo):
    _two_phase_project(repo)
    code, out, err = run_cli(repo, "claim", "P2.T1")
    assert code == REFUSED, (
        f"`next` and `claim` must agree — an item `next` withholds and `claim` grants "
        f"is a race the operator cannot see. exit={code}\n{out}\n{err}"
    )


def test_the_phase_dependency_releases_when_the_phase_completes(repo):
    _two_phase_project(repo)
    _finish(repo, "P1.T1")
    _finish_phase(repo, "P1")
    plan = _plan(repo)
    assert "P2.T1" in _ready(plan), f"P1 is done; P2's tasks must unblock: {plan}"


# -- an umbrella's dependency governs its sub-tasks -----------------------------------


def test_a_sub_task_does_not_start_while_its_umbrella_is_blocked(repo):
    run_cli(repo, "init")
    run_cli(repo, "phase", "add", "P1", "--globs", "core/**")
    run_cli(repo, "task", "add", "P1.T1", "--phase", "P1", "--globs", "core/money.py")
    run_cli(repo, "task", "add", "P1.T2", "--phase", "P1", "--globs", "core/account.py")
    run_cli(
        repo,
        "task",
        "add",
        "P1.T3",
        "--phase",
        "P1",
        "--needs",
        "P1.T1,P1.T2",
        "--globs",
        "core/entry.py",
    )
    run_cli(repo, "task", "add", "P1.T3.a", "--parent", "P1.T3", "--globs", "core/entry.py")

    plan = _plan(repo)
    assert "P1.T3.a" not in _ready(plan), (
        "P1.T3 needs money and account; its sub-task writes the very file that "
        f"depends on them. ready={_ready(plan)}"
    )
    b = _blocked(plan)["P1.T3.a"]
    assert sorted(b["waiting_on"]) == ["P1.T1", "P1.T2"], b

    _finish(repo, "P1.T1")
    plan = _plan(repo)
    assert "P1.T3.a" not in _ready(plan), "one of two dependencies is not both of them"
    _finish(repo, "P1.T2")
    plan = _plan(repo)
    assert "P1.T3.a" in _ready(plan), f"both landed; the sub-task opens: {plan}"


def test_inheritance_is_transitive_through_nested_sub_tasks(repo):
    """A sub-sub-task inherits from the whole chain, not just its immediate parent."""
    run_cli(repo, "init")
    run_cli(repo, "phase", "add", "P1", "--globs", "core/**")
    run_cli(repo, "phase", "add", "P2", "--needs", "P1", "--globs", "api/**")
    run_cli(repo, "task", "add", "P1.T1", "--phase", "P1", "--globs", "core/a.py")
    run_cli(repo, "task", "add", "P2.T1", "--phase", "P2", "--globs", "api/a.py")
    run_cli(repo, "task", "add", "P2.T1.a", "--parent", "P2.T1", "--globs", "api/b.py")
    run_cli(repo, "task", "add", "P2.T1.a.i", "--parent", "P2.T1.a", "--globs", "api/c.py")

    b = _blocked(_plan(repo))["P2.T1.a.i"]
    assert "P1" in b["waiting_on"], (
        f"three levels down, the great-grandparent phase's dependency still binds: {b}"
    )


def test_an_umbrella_needing_its_own_child_does_not_deadlock_that_child(repo):
    """The one inherited dependency that must NOT bind.

    If an umbrella declares a dependency on something beneath it, inheriting that
    downwards makes the child wait for itself, and the subtree can never start —
    a plan typo turned into a permanent hang. A dependency inside your own subtree is
    yours to satisfy by finishing, not to wait on.
    """
    run_cli(repo, "init")
    run_cli(repo, "phase", "add", "P1", "--globs", "core/**")
    run_cli(repo, "task", "add", "P1.T1", "--phase", "P1", "--globs", "core/a.py")
    run_cli(repo, "task", "add", "P1.T1.a", "--parent", "P1.T1", "--globs", "core/b.py")
    run_cli(repo, "update", "P1.T1", "--needs", "P1.T1.a")

    plan = _plan(repo)
    assert "P1.T1.a" in _ready(plan), (
        f"the child satisfies the umbrella's dependency by running, not by waiting: {plan}"
    )


# -- helpers --------------------------------------------------------------------------


def _finish(repo, item):
    assert finish(repo, item)[0] == OK, f"complete {item}"


def _finish_phase(repo, phase):
    assert finish(repo, phase)[0] == OK, f"complete {phase}"


# -- `next` and `claim` must answer the same question ---------------------------------


def test_claim_refuses_an_item_whose_own_dependency_is_open(repo):
    """The plainest case, and it was broken for the plainest reason.

    `acquire` checked removed / done / leased / glob-overlap and never once looked at
    ``needs``. `orchard next` printed "T2: deps — T1 is open" and `orchard claim T2`
    handed out a worktree on the next line. Any agent that picks work by id — which is
    what "implement phase X" does when it walks a plan — skipped the dependency graph
    completely, and the failure is invisible: the work happens, just in the wrong order
    against files that do not exist yet.
    """
    run_cli(repo, "init")
    run_cli(repo, "phase", "add", "P1", "--globs", "core/**")
    run_cli(repo, "task", "add", "T1", "--phase", "P1", "--globs", "core/a.py")
    run_cli(repo, "task", "add", "T2", "--phase", "P1", "--needs", "T1", "--globs", "core/b.py")

    code, out, err = run_cli(repo, "claim", "T2")
    assert code == REFUSED, f"exit={code}\n{out}\n{err}"
    assert "T1" in (out + err), "the refusal must name what it is waiting on"

    code, _, _ = run_cli(repo, "claim", "T2", "--force")
    assert code == OK, "--force is the deliberate override and must still work"


def test_claim_refuses_an_umbrella_and_points_at_its_children(repo):
    run_cli(repo, "init")
    run_cli(repo, "phase", "add", "P1", "--globs", "core/**")
    run_cli(repo, "task", "add", "T1", "--phase", "P1", "--globs", "core/a.py")
    run_cli(repo, "task", "add", "T1.a", "--parent", "T1", "--globs", "core/a.py")
    code, out, err = run_cli(repo, "claim", "T1")
    assert code == REFUSED, f"an umbrella is not work; its children are. exit={code}\n{out}"
    assert "T1.a" in (out + err), f"name the child to work instead: {out}{err}"


def test_next_and_claim_agree_on_every_item_in_a_mixed_plan(repo):
    """The property, not one instance of it: whatever `next` withholds, `claim` refuses.

    Written as a sweep because the two disagreed for three separate reasons at once
    (own deps, inherited phase deps, umbrellas) and fixing one would have left the
    others looking fine.
    """
    run_cli(repo, "init")
    run_cli(repo, "phase", "add", "P1", "--globs", "core/**")
    run_cli(repo, "phase", "add", "P2", "--needs", "P1", "--globs", "api/**")
    run_cli(repo, "task", "add", "P1.T1", "--phase", "P1", "--globs", "core/a.py")
    run_cli(
        repo, "task", "add", "P1.T2", "--phase", "P1", "--needs", "P1.T1", "--globs", "core/b.py"
    )
    run_cli(repo, "task", "add", "P1.T3", "--phase", "P1", "--globs", "core/c.py")
    run_cli(repo, "task", "add", "P1.T3.a", "--parent", "P1.T3", "--globs", "core/c.py")
    run_cli(repo, "task", "add", "P2.T1", "--phase", "P2", "--globs", "api/a.py")

    plan = _plan(repo)
    ready, blocked = set(_ready(plan)), set(_blocked(plan))
    assert ready and blocked, f"the fixture must exercise both sides: {plan}"

    for item in sorted(ready | blocked):
        code, out, err = run_cli(repo, "claim", item, agent=f"probe-{item}")
        want = OK if item in ready else REFUSED
        assert code == want, (
            f"{item}: `next` says {'ready' if item in ready else 'blocked'} but "
            f"`claim` exited {code}\n{out}\n{err}"
        )
        if code == OK:
            run_cli(repo, "release", item, agent=f"probe-{item}")
