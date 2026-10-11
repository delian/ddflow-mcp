"""B-start-timeout-drops-proposal: a start check that runs out of time never goes silent.

The plan of a big log takes seconds, and under load more than `[upgrade].start_timeout_s`. A
timed-out check must leave an actionable NOTE (the upgrade is pending and the operator is
asked), keep the late answer for the next connect, repeat in the brief until resolved, and
show in doctor -- and must never read as "no upgrade needed".
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from test_upgrade_on_start import _load, _seed_old

from ddflow.services import upgrade_plan as UP
from ddflow.services import upgrade_start as US


@pytest.fixture
def old(tmp_path: Path) -> Path:
    return _seed_old(tmp_path)


CHECK = {US.ENV: "check"}
LOCAL = Path(".ddflow") / "local" / "upgrade-start.json"


def _slow_plan(monkeypatch: pytest.MonkeyPatch, delay: float) -> None:
    real = UP.build

    def slow(*a, **k):  # the existing seam: the plan, made slow
        time.sleep(delay)
        return real(*a, **k)

    monkeypatch.setattr(UP, "build", slow)


def _timed_out(old: Path, monkeypatch: pytest.MonkeyPatch, delay: float = 1.0) -> dict:
    _slow_plan(monkeypatch, delay)
    log, cfg, st = _load(old, "starter")
    cfg.upgrade.start_timeout_s = 0.2
    return US.start(old, log, cfg, st, surf="mcp", environ=CHECK)


def _wait_for(done, seconds: float = 10.0) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if done():
            return
        time.sleep(0.05)
    raise AssertionError("the background check never finished")


def test_a_timed_out_check_is_a_note_that_asks_the_operator(old, monkeypatch) -> None:
    rep = _timed_out(old, monkeypatch)
    assert rep["status"] == US.TIMEOUT
    for said in ("newer than this project", "Ask the operator", "did not finish within", "PENDING"):
        assert said in rep["text"], rep["text"]
    assert "ddflow upgrade --plan" in rep["text"]


def test_the_late_answer_is_kept_for_the_next_connect(old, monkeypatch) -> None:
    assert _timed_out(old, monkeypatch)["status"] == US.TIMEOUT
    _wait_for(lambda: US._read(old).get("status") == US.PROPOSED)

    def boom(*a, **k):
        raise AssertionError("the plan was built again")

    monkeypatch.setattr(UP, "build", boom)
    log, cfg, st = _load(old, "starter")
    rep = US.start(old, log, cfg, st, surf="mcp", environ=CHECK)
    assert rep["status"] == US.PROPOSED and "upgrade item(s) are pending" in rep["text"]


def test_a_late_answer_does_not_replace_a_newer_timeout_marker_with_less(old, monkeypatch) -> None:
    rep = _timed_out(old, monkeypatch)
    _wait_for(lambda: US._read(old).get("status") == US.PROPOSED)
    US._remember(old, rep)  # the timeout report arriving after the answer must not undo it
    assert US._read(old)["status"] == US.PROPOSED


def test_the_brief_repeats_an_unfinished_check_until_it_is_resolved(old, monkeypatch) -> None:
    _timed_out(old, monkeypatch, delay=0.5)
    _log, _cfg, st = _load(old, "starter")
    # (the late answer would replace the marker: keep the timeout marker for this test)
    US._save(old, {"version": US.running_version(), "status": US.TIMEOUT, "text": "pending note"})
    assert US.take_for_brief(old, st) == "pending note"
    assert US.take_for_brief(old, st) == "pending note", "said until the upgrade is resolved"
    assert US.take_for_brief(old) == "pending note", "the brief that folds nothing reads it itself"
    st.upgrades = [{"to": "99.0.0"}]  # the operator upgraded
    assert US.take_for_brief(old, st) == ""


def test_doctor_names_an_unfinished_check_until_it_is_resolved(old) -> None:
    _log, _cfg, st = _load(old, "starter")
    assert US.doctor_notes(old, st) == []
    US._save(old, {"version": US.running_version(), "status": US.TIMEOUT, "text": "pending note"})
    assert US.doctor_notes(old, st) == ["pending note"]
    st.upgrades = [{"to": "99.0.0"}]
    assert US.doctor_notes(old, st) == []


def test_a_zero_timeout_is_a_note_not_silence(old) -> None:
    log, cfg, st = _load(old, "starter")
    cfg.upgrade.start_timeout_s = 0
    rep = US.start(old, log, cfg, st, surf="mcp", environ=CHECK)
    assert rep["status"] == US.TIMEOUT and "Ask the operator" in rep["text"]
    assert "start_timeout_s" in rep["text"]


def test_a_slow_plan_never_reads_as_current(old, monkeypatch) -> None:
    for delay in (0.0, 0.6):  # fast: the proposal; slow: the note. Never "".
        _slow_plan(monkeypatch, delay)
        log, cfg, st = _load(old, "starter")
        cfg.upgrade.start_timeout_s = 0.3
        rep = US.start(old, log, cfg, st, surf="mcp", environ=CHECK)
        assert rep["status"] in (US.PROPOSED, US.TIMEOUT) and rep["text"], rep
        assert "Ask the operator" in rep["text"]
        (old / LOCAL).unlink(missing_ok=True)
        time.sleep(0.8)
