"""Bf37f016693: the unit_tests gate runs the whole suite, so it takes the same host-wide
suite lock (and worker floor) as `ci run`; only `ci.py` did, so gates from several agents
ran full suites at once (host load 236)."""

from __future__ import annotations

from ddflow.config import Config
from ddflow.services import suitelock as SL
from ddflow.services.gates import runner as R
from ddflow.services.gates.defs import GateDef


def _cfg(wait: float = 0.0) -> Config:
    cfg = Config()
    cfg.ci.suite_wait_s = wait
    return cfg


def _gate(command: str, gid: str = "unit_tests") -> GateDef:
    return GateDef(id=gid, title=gid, command=command)


def test_a_full_suite_unit_tests_gate_queues_behind_the_holder(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    marker = tmp_path / "ran"
    with SL.hold(_cfg(), "other-agent"):
        outcome, ev = R.run_command_gate(_gate(f"touch {marker}"), tmp_path, cfg=_cfg(0.0))
    assert outcome == "unavailable" and "blocked on" in ev["reason"], ev
    assert not marker.exists(), "the suite started while another held the lock"


def test_a_free_lock_runs_the_gate_and_records_the_wait(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    outcome, ev = R.run_command_gate(_gate("true"), tmp_path, cfg=_cfg())
    assert outcome == "passed" and "waited_s" in ev, ev


def test_a_gate_that_is_not_a_full_suite_never_queues(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    with SL.hold(_cfg(), "other-agent"):
        other, _ = R.run_command_gate(_gate("true", "lint"), tmp_path, cfg=_cfg())
        narrowed, _ = R.run_command_gate(
            _gate("python -c pass tests/test_a.py"), tmp_path, cfg=_cfg()
        )
    assert (other, narrowed) == ("passed", "passed")
    # a command that IS one `ddflow ci ...` takes the lock itself; anything more is a suite
    for ok in ("ddflow ci run --full", "uv run ddflow ci run", "env X=1 /usr/bin/ddflow ci run"):
        assert not R._runs_whole_suite(_gate(ok)), ok
    for locked in (
        "pytest # ddflow ci run",
        "pytest -k 'ddflow ci'",
        "pytest; ddflow ci run",
        "ddflow ci run && pytest",
        "pytest | ddflow ci run",
        "ddflow ci run\npytest",
    ):
        assert R._runs_whole_suite(_gate(locked)), locked


def test_the_holder_floor_reaches_the_gates_worker_count(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    # a host far over its load mark: the clamp alone would give one worker
    monkeypatch.setattr(
        R.WK.S.HostSignals, "sample", lambda self: {"load": 9.0, "memory_free_frac": 0.9}
    )
    cfg = _cfg()
    suite = R.run_command_gate(_gate("true -n {workers}"), tmp_path, cfg=cfg)[1]
    other = R.run_command_gate(_gate("true -n {workers}", "lint"), tmp_path, cfg=cfg)[1]
    assert suite["workers"]["n"] == cfg.ci.suite_min_workers > 1, suite
    assert other["workers"]["n"] == 1, other


def test_the_lock_wait_adds_to_the_admission_wait(tmp_path):
    from types import SimpleNamespace as NS

    p = NS(waited_s=2.0)
    assert R._wait_evidence(_gate("true"), p, "", 8.0) == {"waited_s": 10.0}
    assert R._wait_evidence(_gate("true"), NS(waited_s=0), "", 8.0) == {"waited_s": 8.0}
    assert R._wait_evidence(_gate("true"), NS(waited_s=0), "", None) == {}
