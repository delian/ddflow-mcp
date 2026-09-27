"""B19: after filing work, can `ddflow next` actually offer it?

From the source project: 37 follow-ups — including four confirmed reviewer findings — were
filed where the picker could not see them, and **nothing failed**. Every audit exited 0,
because each measured the items that were present rather than asking whether any could be
picked up.

Two hypotheses about how a TASK could go invisible here were probed and both REFUTED:

  * a task parented to a phase id that does not exist is still reachable, because
    `State.descendants()` is built from the parent FIELD rather than from the items;
  * a task filed with a foreign `kind` is impossible, because `_h_added` takes the kind
    from the event kind and ignores `data`.

So for tasks the property already holds, and `test_every_live_task_is_offerable` pins it
rather than leaving it to be rediscovered. What IS reachable is a phase with nothing
pickable under it, which `next` reports as an empty queue.
"""

from __future__ import annotations

import pytest
from conftest import run_cli

from ddflow.config import Config
from ddflow.core.model import fold
from ddflow.core.schedule import plan, unpickable
from ddflow.infra.log import EventLog


def _state(repo):
    log = EventLog(repo, "a1")
    return log, fold(log.read_all(), strict=False)


def _kinds(repo, cfg=None) -> dict[str, str]:
    _log, st = _state(repo)
    return {u.item: u.kind for u in unpickable(st, cfg or Config.load(repo))}


def test_a_phase_with_no_tasks_is_reported(repo):
    """Work in the queue that `next` will never offer."""
    assert run_cli(repo, "init")[0] == 0
    log, _ = _state(repo)
    log.append("phase.added", "P1", {"title": "not broken down yet", "kind": "phase"})
    assert _kinds(repo) == {"P1": "empty_phase"}


def test_a_phase_whose_tasks_are_all_finished_is_a_problem(repo):
    """Distinct from an empty phase, and never silenceable: a queue held open by an item
    nobody can act on."""
    assert run_cli(repo, "init")[0] == 0
    log, _ = _state(repo)
    log.append("phase.added", "P3", {"title": "done, but still open", "kind": "phase"})
    log.append("task.added", "P3.T1", {"title": "t", "kind": "task", "parent": "P3"})
    log.append("item.completed", "P3.T1", {})
    _log, st = _state(repo)
    found = unpickable(st, Config.load(repo))
    assert [u.kind for u in found] == ["finished_phase"]
    assert found[0].severity == "problem"

    # ...and the knob does NOT silence it, on purpose.
    cfg = Config.load(repo)
    cfg.schedule.empty_phase = "off"
    assert [u.kind for u in unpickable(st, cfg)] == ["finished_phase"]


def test_a_phase_with_live_work_is_not_reported(repo):
    """The other half: a check that fires on a healthy queue gets turned off."""
    assert run_cli(repo, "init")[0] == 0
    log, _ = _state(repo)
    log.append("phase.added", "P1", {"title": "fine", "kind": "phase"})
    log.append("task.added", "P1.T1", {"title": "t", "kind": "task", "parent": "P1"})
    assert _kinds(repo) == {}


def test_a_completed_or_removed_phase_is_not_reported(repo):
    """A phase that is finished or withdrawn is not work anybody is waiting to pick."""
    assert run_cli(repo, "init")[0] == 0
    log, _ = _state(repo)
    for pid, ev in (("PD", "item.completed"), ("PA", "item.abandoned"), ("PR", "phase.removed")):
        log.append("phase.added", pid, {"title": pid, "kind": "phase"})
        log.append(ev, pid, {"reason": "x"} if ev == "item.abandoned" else {})
    assert _kinds(repo) == {}


@pytest.mark.parametrize(
    ("setting", "severity"),
    [("note", "note"), ("problem", "problem")],
)
def test_the_empty_phase_knob_sets_the_severity(repo, setting, severity):
    assert run_cli(repo, "init")[0] == 0
    log, _ = _state(repo)
    log.append("phase.added", "P1", {"title": "empty", "kind": "phase"})
    cfg = Config.load(repo)
    cfg.schedule.empty_phase = setting
    _log, st = _state(repo)
    found = unpickable(st, cfg)
    assert [u.severity for u in found] == [severity]


def test_the_empty_phase_knob_can_silence_it(repo):
    """A project that files phases before breaking them down lives in this state on
    purpose."""
    assert run_cli(repo, "init")[0] == 0
    log, _ = _state(repo)
    log.append("phase.added", "P1", {"title": "empty", "kind": "phase"})
    cfg = Config.load(repo)
    cfg.schedule.empty_phase = "off"
    _log, st = _state(repo)
    assert unpickable(st, cfg) == []


def test_doctor_reports_unpickable_work_and_fails_on_the_serious_kind(repo):
    """A check nothing surfaces is a check nobody runs."""
    assert run_cli(repo, "init")[0] == 0
    log, _ = _state(repo)
    log.append("phase.added", "P1", {"title": "has work", "kind": "phase"})
    log.append("task.added", "P1.T1", {"title": "t", "kind": "task", "parent": "P1"})
    rc, out, _err = run_cli(repo, "doctor")
    assert rc == 0, out

    # An empty phase is a NOTE by default: mentioned, does not fail.
    log.append("phase.added", "P2", {"title": "empty", "kind": "phase"})
    rc, out, _err = run_cli(repo, "doctor")
    assert "P2" in out, out
    assert rc == 0, f"an empty phase should not fail doctor by default:\n{out}"

    # A phase over finished work does fail.
    log.append("phase.added", "P3", {"title": "finished", "kind": "phase"})
    log.append("task.added", "P3.T1", {"title": "t", "kind": "task", "parent": "P3"})
    log.append("item.completed", "P3.T1", {})
    rc, out, _err = run_cli(repo, "doctor")
    assert rc != 0, f"a phase open over finished work should fail doctor:\n{out}"
    assert "P3" in out


def test_every_live_task_is_offerable(repo):
    """The INVARIANT behind the two refuted hypotheses, pinned.

    `plan()` with no phase must offer, run or block EVERY live task — never simply omit
    one. This is what makes "filed" and "pickable" the same thing for tasks, and it is a
    property a future `kind` filter or index-based walk could quietly remove.
    """
    assert run_cli(repo, "init")[0] == 0
    log, _ = _state(repo)
    log.append("phase.added", "P1", {"title": "p", "kind": "phase"})
    log.append("task.added", "P1.T1", {"title": "normal", "kind": "task", "parent": "P1"})
    # A task parented to a phase that does not exist — hypothesis 1.
    log.append("task.added", "P9.T1", {"title": "orphan", "kind": "task", "parent": "P9"})
    # A task whose `data` claims a foreign kind — hypothesis 2. `_h_added` must ignore it.
    log.append("task.added", "ODD", {"title": "odd", "kind": "chore", "parent": "P1"})
    # A task blocked on something that does not exist: must be BLOCKED, not absent.
    log.append(
        "task.added", "P1.T2", {"title": "n", "kind": "task", "parent": "P1", "needs": ["NOPE"]}
    )

    _log, st = _state(repo)
    assert st.items["ODD"].kind == "task", "a task.added event produced a non-task item"
    live = {i.id for i in st.tasks() if not i.removed}
    p = plan(st, Config.load(repo))
    seen = {i.id for i in p.ready} | {i.id for i in p.running} | {b.item for b in p.blocked}
    assert live <= seen, f"filed but invisible to the picker: {sorted(live - seen)}"
    assert "P1.T2" in {b.item for b in p.blocked}, "an unknown dependency must BLOCK, not hide"


def test_a_phase_scoped_call_still_reaches_a_task_under_an_absent_parent(repo):
    """Hypothesis 1, pinned: `descendants()` walks the parent FIELD, so a typo'd phase id
    is still reachable by `next --phase <that id>`."""
    assert run_cli(repo, "init")[0] == 0
    log, _ = _state(repo)
    log.append("task.added", "P9.T1", {"title": "orphan", "kind": "task", "parent": "P9"})
    _log, st = _state(repo)
    p = plan(st, Config.load(repo), phase="P9")
    assert [i.id for i in p.ready] == ["P9.T1"]
