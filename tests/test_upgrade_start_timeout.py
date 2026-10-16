"""B-start-timeout-drops-proposal: a start check that runs out of time never goes silent.

The plan of a big log takes seconds, and under load more than `[upgrade].start_timeout_s`. A
timed-out check must leave an actionable NOTE (the upgrade is pending and the operator is
asked), keep the late answer for the next connect, repeat in the brief until resolved, and
show in doctor -- and must never read as "no upgrade needed".
"""

from __future__ import annotations

import time
from pathlib import Path

import _wait
import pytest
from _wait import scaled, wait_until
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


def _wait_for(done) -> None:
    wait_until(done, 10, what="the background check finishing")


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
    _wait_for(lambda: US._read(old).get("status") == US.PROPOSED)  # the thread is done
    (old / LOCAL).unlink()  # start again from a marker that holds only the note
    _log, _cfg, st = _load(old, "starter")
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


def test_the_state_that_cannot_be_read_still_delivers_the_note_once(old, monkeypatch) -> None:
    US._save(old, {"version": US.running_version(), "status": US.TIMEOUT, "text": "pending note"})
    monkeypatch.setattr(US, "_state_of", lambda repo: None)
    assert US.take_for_brief(old) == "pending note"
    assert US.take_for_brief(old) == "", "said once when it cannot tell whether it still holds"


def test_a_late_answer_and_the_timeout_note_cannot_undo_each_other(old, monkeypatch) -> None:
    """The timeout report is saved while the left-running thread saves the answer it reached:
    whichever order the reads and writes interleave in, the finished answer is what is kept."""
    import threading

    key = "k"
    answer = {"status": US.PROPOSED, "text": "the plan", "version": "9", "key": key}
    note = {"status": US.TIMEOUT, "text": "the note", "version": "9", "key": key}
    reading, release = threading.Event(), threading.Event()
    real_read = US._read

    def slow_read(repo):  # the note's writer has read the marker and is slow to write
        got = real_read(repo)
        if threading.current_thread().name == "note-writer":
            reading.set()
            release.wait(2)
        return got

    monkeypatch.setattr(US, "_read", slow_read)
    writer = threading.Thread(target=US._save, args=(old, note), name="note-writer")
    writer.start()
    assert reading.wait(5)
    late = threading.Thread(target=US._save, args=(old, answer))
    late.start()  # the late answer arrives while the note is between its read and its write
    time.sleep(0.2)
    release.set()
    writer.join(5)
    late.join(5)
    assert real_read(old)["status"] == US.PROPOSED


def test_a_kept_proposal_does_not_depend_on_who_connected(old, monkeypatch) -> None:
    _slow_plan(monkeypatch, 0.6)
    log, cfg, st = _load(old, "agent-a")
    cfg.upgrade.start_timeout_s = 0.2
    assert US.start(old, log, cfg, st, agent="agent-a", surf="mcp", environ=CHECK)["status"] == (
        US.TIMEOUT
    )
    _wait_for(lambda: US._read(old).get("status") == US.PROPOSED)
    kept = US._read(old)["text"]

    def boom(*a, **k):
        raise AssertionError("the plan was built again")

    monkeypatch.setattr(UP, "build", boom)
    log, cfg, st = _load(old, "agent-b")
    rep = US.start(old, log, cfg, st, agent="agent-b", surf="mcp", environ=CHECK)
    assert rep["text"] == kept and "agent-a" not in kept and "agent-b" not in kept


def _container_start(old: Path, timeout_s: float) -> dict:
    log, cfg, st = _load(old, "starter")
    cfg.upgrade.start_timeout_s = timeout_s
    return US.start(old, log, cfg, st, agent="starter", surf="container", environ={})


def _assert_applied_and_not_kept(old: Path, rep: dict) -> None:
    assert rep["status"] == US.APPLIED, rep["text"]
    assert US._read(old).get("complete") is False
    assert US._cached(old, US._key(US.running_version(), "", "safe", "container", True)) is None


def test_a_container_start_that_applied_something_is_never_kept(old) -> None:
    """The deadline is not what this test is about, so it is generous: under load average
    300-600 the default ran out before the apply finished and the status read 'timeout'
    (B53b653895f)."""
    _assert_applied_and_not_kept(old, _container_start(old, scaled(10)))


def test_a_slow_apply_still_reads_as_applied_when_the_deadline_scales(old, monkeypatch) -> None:
    """The slow path forced: the plan takes 1 s while the deadline is scaled(0.3) on a
    saturated host. A fixed 0.3 s deadline (the old shape) reads 'timeout'."""
    _slow_plan(monkeypatch, 1.0)
    monkeypatch.setattr(_wait.os, "getloadavg", lambda: (1000.0, 0.0, 0.0))
    _assert_applied_and_not_kept(old, _container_start(old, scaled(0.3)))


def test_a_failure_report_does_not_replace_a_kept_answer(old, monkeypatch) -> None:
    _timed_out(old, monkeypatch)
    _wait_for(lambda: US._read(old).get("status") == US.PROPOSED)
    US._remember(old, {"version": US.running_version(), "status": US.FAILED, "text": "it failed"})
    assert US._read(old)["status"] == US.PROPOSED


def test_a_read_only_answer_is_not_replayed_once_the_project_is_writable(old, monkeypatch) -> None:
    monkeypatch.setattr(US, "_writable", lambda repo: False)
    log, cfg, st = _load(old, "starter")
    assert US.start(old, log, cfg, st, surf="mcp", environ={})["status"] == US.READONLY
    monkeypatch.undo()  # the project can be written now
    log, cfg, st = _load(old, "starter")
    rep = US.start(old, log, cfg, st, surf="mcp", environ={})
    assert rep["status"] != US.READONLY and "not writable" not in rep["text"]


def test_a_check_the_operator_switched_off_is_told_once_not_on_every_brief(old) -> None:
    log, cfg, st = _load(old, "starter")
    cfg.upgrade.start_timeout_s = 0
    US.start(old, log, cfg, st, surf="mcp", environ=CHECK)
    assert "switched off" in US.take_for_brief(old, st)
    assert US.take_for_brief(old, st) == ""


def test_the_timeout_note_does_not_claim_a_container_applied_nothing_forever(old) -> None:
    note = US._timeout_note("9.9.9", "0.1.0", 10)
    assert "nothing had been applied when it started" in note and "still be running" in note
