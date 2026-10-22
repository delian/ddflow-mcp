"""B1d51fb9da1: the time a gate queued for the host lock is recorded and readable, apart
from the time it ran; a log from before the field replays and reports no figure, not a zero."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest
from lanes_fixture import T0, Log
from metrics_fixture import frozen

from ddflow.services import ci_marker as CM
from ddflow.services import metrics as M
from ddflow.services.metrics import facts as F


def _log(waited: list[float | None], gate: str = "ci") -> Log:
    log = Log()
    log.task("T1", "a.py")
    log.claim("a1", "T1", T0, "a.py")
    t = T0
    for w in waited:
        t += 4000
        log.add("gate.started", "T1", "a1", t, gate=gate)
        ev: dict = {"elapsed_s": 3000.0, "exit": 0}
        if w is not None:
            ev["waited_s"] = w
        log.add("gate.passed", "T1", "a1", t + 3000, gate=gate, by="a1", evidence=ev)
        t += 3000
    return log


def _ci_row(log: Log, gate: str = "ci"):
    st, c, now = frozen(log)
    c.metrics.min_samples = 1
    return {r["gate"]: r for r in M.run(log.events, st, c, now).metrics["gate_time"]}[gate]


def test_a_recorded_wait_is_readable_in_the_facts_and_in_metrics():
    log = _log([1700.0, 300.0])
    st, _cfg, _now = frozen(log)
    assert [o.waited_s for o in F.collect(log.events, st).outcomes if o.gate == "ci"] == [
        1700.0,
        300.0,
    ]
    row = _ci_row(log)
    assert row["wait_n"] == 2 and row["wait_max_s"] == 1700.0


def test_a_log_without_the_field_replays_and_reports_no_wait_not_zero():
    row = _ci_row(_log([None]))
    assert row["n"] == 1 and row["wait_n"] == 0
    assert all(row[k] is None for k in row if k.startswith("wait_") and k != "wait_n")
    st, _c, _now = frozen(_log([None]))
    assert [o.waited_s for o in F.collect(_log([None]).events, st).outcomes] == [None]


def test_the_wait_line_roundtrips_and_the_last_one_wins():
    assert CM.parse_wait(CM.wait_line(12.5)) == 12.5
    assert CM.parse_wait(CM.wait_line(0.04)) == 0.04
    assert CM.parse_wait(f"x\n{CM.wait_line(3)}\n{CM.wait_line(7.0)}\ny") == 7.0
    assert CM.parse_wait("no marker") is None
    assert CM.parse_wait(CM.wait_line(1_000_000.0)) == 1_000_000.0  # no exponent notation


def test_a_ci_result_carries_the_wait_only_when_it_queued():
    from ddflow.services import ci as CI

    res = CI.Result("passed")
    assert "waited_s" not in res.as_data()
    res.waited_s = 7.5
    assert res.as_data()["waited_s"] == 7.5


def test_the_runner_records_waited_s_from_the_ci_wait_line_and_the_slot_wait():
    from ddflow.services.cmdrunner import CommandRun
    from ddflow.services.gates import runner as R

    gdef = type("G", (), {"id": "ci"})()
    p = CommandRun("ran", "x", code=0, elapsed_s=5.0, waited_s=0.0)
    assert R._waited(gdef, p, f"out\n{CM.wait_line(1700.4)}\n") == pytest.approx(1700.4)
    assert R._waited(gdef, p, "out") is None
    slot = CommandRun("ran", "x", code=0, elapsed_s=5.0, waited_s=3.2)
    assert R._waited(type("G", (), {"id": "unit_tests"})(), slot, "") == 3.2


def test_gate_status_shows_how_long_a_run_queued_for_the_host_lock():
    from ddflow.config import Config
    from ddflow.core.model import fold
    from ddflow.services.gates import outcomes as O

    log = _log([1700.0], "research")
    st = fold(list(log.events), strict=False)
    s = O.status(st, Config(), "T1")
    assert s.queued == {"research": "queued 28m of 50m before it ran"}
    half = O.status(fold(list(_log([0.5], "research").events), strict=False), Config(), "T1")
    assert half.queued == {"research": "queued 0.5s of 50m before it ran"}
    assert "queued 28m of 50m before it ran" in s.render()
    assert (
        O.status(fold(list(_log([None], "research").events), strict=False), Config(), "T1").queued
        == {}
    )


def test_a_sub_tenth_second_slot_wait_is_still_a_recorded_wait():
    from ddflow.services.cmdrunner import CommandRun
    from ddflow.services.gates import runner as R

    p = CommandRun("ran", "x", code=0, elapsed_s=5.0, waited_s=0.04)
    assert (
        R._waited(type("G", (), {"id": "unit_tests"})(), p, "") == 0.04
    )  # millisecond precision: a real wait is never rounded to zero


def test_metrics_render_shows_the_wait_beside_the_gate_time():
    from ddflow.services.metrics import render as RD

    row = _ci_row(_log([1700.0, 300.0]))
    text = RD.summary({**_blank(), "gate_time": [row]})
    assert "queued before running: n=2" in text


def test_a_slot_only_wait_of_a_non_ci_gate_is_labelled_as_queueing_not_as_the_lock():
    from ddflow.services.metrics import render as RD

    row = _ci_row(_log([30.0], "unit_tests"), "unit_tests")
    text = RD.summary({**_blank(), "gate_time": [row]})
    assert row["gate"] == "unit_tests" and row["wait_n"] == 1 and row["wait_max_s"] == 30.0
    assert "queued before running: n=1 p50 30s max 30s" in text
    assert "for the lock" not in text


def _blank() -> dict:
    s = {"n": 0, "p50_s": None, "p90_s": None, "max_s": None}
    return {
        "cycle_time": s, "review_rounds": {"n": 0, "p50_rounds": None, "p90_rounds": None, "max_rounds": None},
        "queue_wait": s, "lease_wait": s, "rework": {"reworked": 0, "completed": 0, "rate": None},
        "gate_time": [], "group_by": "gate", "outcomes": [], "providers": [], "unavailable": {},
    }  # fmt: skip
