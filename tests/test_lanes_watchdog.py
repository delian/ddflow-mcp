"""The lanes watchdog: stall reasons, per-cause remedies and recover-auto decisions.

Six silent deaths, each a different kind of corpse, each recovered without losing work.
The lane sheet names the step that killed a stopped lane (its last event kind and the
gate that was mid-run) and attaches a per-cause remedy; `recover_auto.decide` then says
whether ddflow can settle the corpse alone -- and, for the ones it cannot (an adopted
harness tree, a gate only the operator answers, work it could not measure), it must say
so and never touch them.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lanes_fixture import T0, Log, build_inputs

from ddflow.services import lanes as LN
from ddflow.services.lanes import states as S
from ddflow.services.lanes.recover_auto import (
    AUTO_RESUME,
    EXPIRE,
    STAY,
    RecoverAuto,
    decide,
    decisions,
)

NOW = T0 + 5000  # every TTL (1800 s) has lapsed: each lane below is a stopped corpse


def _ledger(g: Log, now: float = NOW):
    st, cfg, plan = build_inputs(g.events, now)
    return LN.build(g.events, st, cfg, now, plan)


# -- six silent deaths in the lane sheet -----------------------------------------------


def test_a_corpse_names_the_step_that_killed_it():
    """A lane that started a gate and never recorded an outcome died inside that gate."""
    g = Log()
    g.task("G1", "g1.py")
    g.claim("lane-gate", "G1", T0, "g1.py")
    g.add("gate.started", "G1", "lane-gate", T0 + 500, gate="unit_tests")
    (lane,) = _ledger(g)
    assert lane.state == S.STOPPED_UNKNOWN
    assert lane.last_event_kind == "gate.started"
    assert lane.mid_gate == "unit_tests"
    assert "unit_tests" in lane.remedy and "ddflow claim G1" in lane.remedy


def test_a_heartbeat_only_corpse_names_its_silence():
    """A lane that only renewed its lease and never did a step of work."""
    g = Log()
    g.task("H2", "h2.py")
    g.claim("lane-beat", "H2", T0, "h2.py")
    g.beat("lane-beat", "H2", T0 + 300)
    (lane,) = _ledger(g)
    assert lane.state == S.STOPPED_UNKNOWN and lane.last_event_kind == "lease.renewed"
    assert lane.mid_gate == ""
    assert "heartbeats only" in lane.remedy and "ddflow claim H2" in lane.remedy


def test_a_session_ended_corpse_names_the_unfinished_item():
    g = Log()
    g.task("E3", "e3.py")
    g.session("lane-ended", "s1", T0 - 300, ended=T0 + 100, tool="codex")
    g.claim("lane-ended", "E3", T0, "e3.py")
    (lane,) = _ledger(g)
    assert lane.state == S.STOPPED_ENDED
    assert "E3" in lane.remedy and "ddflow claim E3" in lane.remedy


def test_a_session_ended_mid_gate_names_that_gate():
    """A session that ended while a gate was mid-run names the gate in its remedy."""
    g = Log()
    g.task("G3", "g3.py")
    g.session("lane-ended-gate", "s1", T0 - 300, ended=T0 + 700, tool="codex")
    g.claim("lane-ended-gate", "G3", T0, "g3.py")
    g.add("gate.started", "G3", "lane-ended-gate", T0 + 500, gate="critic")
    (lane,) = _ledger(g)
    assert lane.state == S.STOPPED_ENDED and lane.mid_gate == "critic"
    assert "critic" in lane.remedy


def test_a_corpse_names_its_own_gate_not_the_takeover_lanes():
    """A corpse lane names the gate IT started, not the gate the lane that later took
    over its item started (B963975258d: _mid_gates keyed by item collides)."""
    g = Log()
    g.task("I", "i.py")
    g.claim("lane-gate", "I", T0, "i.py")
    g.add("gate.started", "I", "lane-gate", T0 + 500, gate="unit_tests")
    g.claim("lane-take", "I", T0 + 600, "i.py")  # recover re-lease: a second lane takes I
    g.add("gate.started", "I", "lane-take", T0 + 700, gate="ci")
    lanes = {lane.name: lane for lane in _ledger(g)}
    assert lanes["lane-gate"].mid_gate == "unit_tests"
    assert lanes["lane-take"].mid_gate == "ci"


def test_the_sheet_render_attaches_the_remedy_to_a_stopped_lane():
    g = Log()
    g.task("H2", "h2.py")
    g.claim("lane-beat", "H2", T0, "h2.py")
    g.beat("lane-beat", "H2", T0 + 300)
    text = LN.render_ledger(_ledger(g), NOW)
    assert "lane-beat:" in text and "resume" in text


# -- recover-auto decisions ------------------------------------------------------------


def _row(item, lane, salvageable, **kw):
    return {"item": item, "lane": lane, "salvageable": salvageable, **kw}


def test_recover_auto_resumes_an_expired_lease_over_salvageable_work():
    d = decide("T1", "lane-a", salvageable=True)
    assert d.effect == AUTO_RESUME and d.auto is True
    assert d.marker and "kept" in d.rationale


def test_recover_auto_expires_a_clean_expired_lease():
    d = decide("T1", "lane-a", salvageable=False)
    assert d.effect == EXPIRE and d.auto is True and "nothing is lost" in d.rationale


def test_recover_auto_never_touches_an_adopted_harness_tree():
    d = decide("T1", "lane-a", salvageable=True, adopted=True)
    assert d.effect == STAY and d.auto is False and "adopted" in d.rationale


def test_recover_auto_never_touches_an_operator_answer_gate():
    d = decide("T1", "lane-a", salvageable=True, operator_answer=True)
    assert d.effect == STAY and d.auto is False and "operator" in d.rationale


def test_recover_auto_never_touches_an_unmeasurable_tree():
    d = decide("T1", "lane-a", salvageable=None)
    assert d.effect == STAY and d.auto is False


def test_six_silent_deaths_are_all_losslessly_recovered():
    """Every death ends in a decision that keeps the work. The three the machine may
    revive itself are auto_applied; the three it may not (adopted, operator-answer,
    unmeasurable) are explicitly left alone -- nothing is deleted on any branch."""
    rows = [
        _row("G1", "lane-gate", True),  # died inside a gate: revived
        _row("H2", "lane-beat", True),  # heartbeat-only: revived
        _row("E3", "lane-ended", True),  # session ended mid-work: revived
        _row("A4", "lane-adopted", True, adopted=True),  # adopted harness tree: left
        _row("O5", "lane-op", True, operator_answer=True),  # operator answer needed: left
        _row("U6", "lane-unknown", None),  # unmeasurable: left
    ]
    got = decisions(rows)
    assert [d.item for d in got] == [r["item"] for r in rows]
    assert [d.effect for d in got] == [AUTO_RESUME, AUTO_RESUME, AUTO_RESUME, STAY, STAY, STAY]
    assert sum(d.auto for d in got[:3]) == 3  # the three revivable corpses are auto
    assert all(not d.auto for d in got[3:])  # the three guarded corpses are never auto
    # Losslessness: an auto action keeps the work, a stay never deletes it either.
    for d in got:
        assert "kept" in d.rationale or "nothing is lost" in d.rationale or d.effect == STAY


def test_recover_auto_a_present_false_adopted_beats_the_adopted_items_fallback():
    """A row's own ``adopted`` wins where both exist, even when it is False: a lane
    measured as not-adopted is refused auto-resume rather than silently borrowed."""
    rows = [
        _row("T1", "lane-not-adopted", True, adopted=False),  # own False -> not adopted
        _row("T2", "lane-borrowed", True),  # no own flag -> borrows the fallback True
    ]
    got = decisions(rows, adopted_items={"T1": True, "T2": True})
    assert got[0].effect == AUTO_RESUME  # False won: salvageable and not adopted
    assert got[1].effect == STAY  # fallback adopted: left alone


def test_recover_auto_adopted_items_fills_only_a_missing_flag():
    """A row with no ``adopted`` key borrows the caller's answer, instead of default-TRUE."""
    rows = [_row("T1", "lane-a", True), _row("T2", "lane-b", True)]
    got = decisions(rows, adopted_items={"T1": True, "T2": False})
    assert got[0].effect == STAY  # inherited adopted
    assert got[1].effect == AUTO_RESUME  # inherited not-adopted -> salvageable work resumes


def test_recover_auto_decisions_serialise_every_verdict():
    d = decide("T1", "lane-a", salvageable=True)
    assert set(d.as_dict()) == {"item", "lane", "effect", "rationale", "auto", "marker"}
    assert isinstance(RecoverAuto, type)
