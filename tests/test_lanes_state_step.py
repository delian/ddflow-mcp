"""The lane STEP column: which pipeline gate each lane is on.

The ledger carries ``mid_gate`` and ``last_event_kind``; the STEP surfaces the lane's
current step -- the in-flight gate, else the next unsatisfied gate of the held item, else
the last event kind -- in the ``ddflow lanes`` state table and in the ``as_dict`` wire.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lanes_fixture import T0, Log, build_inputs

from ddflow.services import lanes as LN

NOW = T0 + 60


def _ledger(g: Log, now: float = NOW):
    st, cfg, plan = build_inputs(g.events, now)
    return LN.build(g.events, st, cfg, now, plan)


def test_a_lane_carries_a_step_field():
    assert LN.Lane("a", step="unit_tests").step == "unit_tests"


def test_step_names_the_in_flight_gate_of_the_held_item():
    g = Log()
    g.task("X", "f.py")
    g.claim("a", "X", T0, "f.py")
    g.add("gate.started", "X", "a", T0 + 10, gate="unit_tests")
    lane = _ledger(g)[0]
    assert lane.step == "unit_tests"
    assert lane.as_dict(T0 + 60)["step"] == "unit_tests"


def test_step_names_the_next_unfinished_gate_before_any_starts():
    g = Log()
    g.task("X", "f.py")
    g.claim("a", "X", T0, "f.py")
    assert _ledger(g)[0].step == "research"


def test_step_advances_past_satisfied_gates():
    g = Log()
    g.task("X", "f.py")
    g.claim("a", "X", T0, "f.py")
    g.add("gate.passed", "X", "a", T0 + 10, gate="research")
    assert _ledger(g)[0].step == "rules"


def test_step_falls_back_to_last_event_kind_when_it_holds_nothing():
    g = Log()
    g.task("X", "f.py")
    g.claim("a", "X", T0, "f.py")
    g.release("a", "X", T0 + 30)
    assert _ledger(g)[0].step == "lease.released"


def test_render_ledger_names_a_step_column():
    g = Log()
    g.task("X", "f.py")
    g.claim("a", "X", T0, "f.py")
    g.add("gate.started", "X", "a", T0 + 10, gate="unit_tests")
    text = LN.render_ledger(_ledger(g), T0 + 60)
    assert "STEP" in text
    assert "unit_tests" in text
