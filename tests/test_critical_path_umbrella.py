"""critical_path counts an umbrella's open sub-tasks (bug B79c2f6e17a).

P1.T1 split into sub-task P1.T1a, and P1.T2 needs P1.T1: T1 closes only when T1a does,
so the chain is T1a -> T1 -> T2. The path walked `needs` (inherited) alone and reported
two steps -- one short per level of nesting, on the number meant to stop someone adding
an agent to a phase whose runtime a chain sets.
"""

from __future__ import annotations

from ddflow.core.model import fold
from ddflow.core.schedule import critical_path


def _add(log, tid, parent, needs=()):
    log.append("task.added", tid, {"parent": parent, "needs": list(needs), "globs": [tid]})


def test_an_umbrellas_sub_task_is_a_step_before_it(log):
    log.append("phase.added", "P1", {"title": "p"})
    _add(log, "P1.T1", "P1")
    _add(log, "P1.T1a", "P1.T1")
    _add(log, "P1.T2", "P1", ["P1.T1"])
    st = fold(log.read_all())
    assert critical_path(st, "P1") == ["P1.T1a", "P1.T1", "P1.T2"]
    assert critical_path(st) == ["P1.T1a", "P1.T1", "P1.T2"]


def test_nested_umbrellas_count_every_level_and_the_longest_child_chain(log):
    log.append("phase.added", "P1", {"title": "p"})
    _add(log, "T", "P1")
    _add(log, "T.a", "T")
    _add(log, "T.a.x", "T.a")
    _add(log, "T.a.y", "T.a", ["T.a.x"])
    _add(log, "T.b", "T")
    _add(log, "U", "P1", ["T"])
    st = fold(log.read_all())
    assert critical_path(st, "P1") == ["T.a.x", "T.a.y", "T.a", "T", "U"]


def test_a_finished_sub_task_is_no_step(log):
    log.append("phase.added", "P1", {"title": "p"})
    _add(log, "P1.T1", "P1")
    _add(log, "P1.T1a", "P1.T1")
    _add(log, "P1.T1b", "P1.T1")
    _add(log, "P1.T2", "P1", ["P1.T1"])
    log.append("item.completed", "P1.T1a", {})
    st = fold(log.read_all())
    assert critical_path(st, "P1") == ["P1.T1b", "P1.T1", "P1.T2"]


def test_a_phase_another_depends_on_is_its_whole_chain_and_then_a_step(log):
    """Reviewers' probe: P2 needs P1, and P1 holds A -> B -> C -> D. P2's task waits on
    all of it; the phase boundary is a step, the phase at the end is not."""
    log.append("phase.added", "P1", {"title": "one"})
    log.append("phase.added", "P2", {"title": "two", "needs": ["P1"]})
    _add(log, "A", "P1")
    _add(log, "B", "P1", ["A"])
    _add(log, "C", "P1", ["B"])
    _add(log, "D", "P1", ["C"])
    _add(log, "T2", "P2")
    st = fold(log.read_all())
    assert critical_path(st) == ["A", "B", "C", "D", "P1", "T2"]
    assert critical_path(st, "P1") == ["A", "B", "C", "D"]
    # Scoped to P2, the floor still runs through what P2 waits on (roborev).
    assert critical_path(st, "P2") == ["A", "B", "C", "D", "P1", "T2"]


def test_trailing_phases_do_not_win_against_a_longer_task_chain(log):
    """Reviewers' probe: phase P1 holding A gives a chain A, P1 as long as B -> C; picked
    first and trimmed afterwards it reported [A] while B -> C was the real floor."""
    log.append("phase.added", "P1", {"title": "one"})
    _add(log, "A", "P1")
    _add(log, "B", "")
    _add(log, "C", "", ["B"])
    st = fold(log.read_all())
    assert critical_path(st) == ["B", "C"]
