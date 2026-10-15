"""A loop through finished work is no cycle for the forecast or `next`; a real one is named (bug Bbbcbfc4bbb).

The forecast's longest chain looked for cycles over EVERY live item, finished ones too, and
with parent-contains-child edges added; a loop that ran only through finished work (which
`next` rightly ignores) made the whole-project forecast refuse with "a cycle" and no name.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lanes_fixture import NOBODY, T0, Log
from metrics_fixture import frozen, twelve

from ddflow.core.graph import find_cycles
from ddflow.core.schedule import critical_path, plan
from ddflow.services import metrics as M


def _forecast(log: Log, phase: str = ""):
    st, cfg, now = frozen(log)
    return M.run(log.events, st, cfg, now, view="forecast", phase=phase).metrics


def _next_cycles(log: Log) -> list[list[str]]:
    st, cfg, now = frozen(log)
    return plan(st, cfg, now=now, agent=NOBODY).cycles


def _chain(log: Log) -> None:
    log.task("C0", "c0.py")
    log.task("C1", "c1.py", needs="C0")


def _finished_loop_through_a_child(log: Log) -> None:
    """Y (umbrella) <- X needs Y <- Z, a child of Y, needs X: a loop only if a parent waits
    on its child; all three are done, so nothing is stuck."""
    log.task("Y", "y.py")
    log.task("X", "x.py", needs="Y")
    log.add(
        "task.added",
        "Z",
        "lead",
        T0 - 900,
        title="z",
        body="z",
        globs=["z.py"],
        needs=["X"],
        parent="Y",
    )
    for t in ("Z", "X", "Y"):
        log.complete("a1", t, T0 + 1)


def test_a_loop_through_finished_work_is_no_cycle_for_the_forecast():
    log = twelve()
    _finished_loop_through_a_child(log)
    _chain(log)
    assert _next_cycles(log) == []
    m = _forecast(log)
    assert m["available"] and m["chain"] == ["C0", "C1"], m["reason"]


def test_a_real_cycle_is_named_by_every_view():
    log = twelve()
    log.task("X1", "x1.py", needs="X2")
    log.task("X2", "x2.py", needs="X1")
    st, _cfg, _now = frozen(log)
    assert critical_path(st) == []
    m = _forecast(log)
    assert not m["available"] and "cycle" in m["reason"]
    assert "X1" in m["reason"] and "X2" in m["reason"]
    named = {n for c in _next_cycles(log) for n in c}
    assert {"X1", "X2"} <= named


def test_a_phase_needing_its_own_child_is_not_a_cycle_anywhere():
    log = twelve()
    log.add("phase.added", "P", "lead", T0 - 900, title="p", needs=["P.T1"])
    log.add("task.added", "P.T1", "lead", T0 - 800, title="t", body="t", globs=["t.py"], parent="P")
    st, _cfg, _now = frozen(log)
    assert _next_cycles(log) == []
    assert find_cycles(st.live_by_id()) == []
    m = _forecast(log)
    assert m["available"] and m["chain"] == ["P.T1"], m["reason"]
