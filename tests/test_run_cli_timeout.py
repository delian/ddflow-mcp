"""Bfaef48ced6: the ceiling of one CLI call in a test grows with the load of the machine."""

from __future__ import annotations

import subprocess

import _wait
import pytest
from conftest import RUN_CLI_TIMEOUT_S, run_cli


def _capture(monkeypatch) -> dict:
    seen: dict = {}

    def fake(argv, **kw):
        seen.update(kw)
        return subprocess.CompletedProcess(argv, 0, "out", "err")

    monkeypatch.setattr(subprocess, "run", fake)
    return seen


def test_a_quiet_machine_keeps_the_base_ceiling(monkeypatch, tmp_path):
    monkeypatch.setattr(_wait.os, "getloadavg", lambda: (0.0, 0.0, 0.0))
    monkeypatch.delenv("DDFLOW_TEST_TIME_SCALE", raising=False)
    seen = _capture(monkeypatch)
    assert run_cli(tmp_path, "status") == (0, "out", "err")
    assert seen["timeout"] == RUN_CLI_TIMEOUT_S


def test_a_saturated_machine_gets_a_longer_ceiling(monkeypatch, tmp_path):
    """The slow path forced: the fixed 300 s ceiling (the old shape) fails this."""
    _wait.pretend_saturated(monkeypatch)
    seen = _capture(monkeypatch)
    run_cli(tmp_path, "status")
    assert seen["timeout"] >= RUN_CLI_TIMEOUT_S * 10


def test_a_timed_out_call_names_the_command(monkeypatch, tmp_path):
    def hang(argv, **kw):
        raise subprocess.TimeoutExpired(argv, kw["timeout"])

    monkeypatch.setattr(subprocess, "run", hang)
    with pytest.raises(AssertionError, match=r"`ddflow task add T1` did not finish within"):
        run_cli(tmp_path, "task", "add", "T1")
