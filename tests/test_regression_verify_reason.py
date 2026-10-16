"""A regression run that did not pass says WHY (bug B1a74291621).

`complete --regression-test` answered "does not pass on the fixed tree (failed)" with no
exit code, no output and no hint that the machine was at load average 200 -- a slow test
that missed its own timeout read as a verdict on the test.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_bug_regression_verified import _case

from ddflow import api
from ddflow.services.gates import mutation as M
from ddflow.services.gates import regression_diag as RD

OK, REFUSED = 0, 3
TEST = "tests/test_f.py::test_f"


def _fix_run_returns(monkeypatch, outcome, ev):
    real = M.run_command_gate
    calls = []

    def fake(gdef, cwd, **kw):
        calls.append(cwd)
        return (outcome, ev) if len(calls) == 1 else real(gdef, cwd, **kw)

    monkeypatch.setattr(M, "run_command_gate", fake)


def test_a_failed_fix_run_shows_the_verdict_the_exit_code_and_the_last_lines(repo, monkeypatch):
    _case(repo)
    _fix_run_returns(
        monkeypatch,
        "failed",
        {
            "exit": 1,
            "elapsed_s": 11.2,
            "summary": ["1 failed, 3 passed in 11.20s"],
            "tail": "noise\nE   assert 0 == 1\nFAILED tests/test_f.py::test_f - assert 0 == 1\n",
        },
    )
    out = api.bug_fixed(repo, "B1", regression_test=TEST)
    assert out.exit == REFUSED, out
    for want in ("(failed: exit 1 after 11.2s)", "1 failed, 3 passed", "assert 0 == 1"):
        assert want in out.reason, (want, out.reason)


def test_a_loaded_machine_is_named_and_the_rerun_advised(repo, monkeypatch):
    _case(repo)
    monkeypatch.setattr(os, "getloadavg", lambda: (192.0, 150.0, 90.0))
    monkeypatch.setattr(os, "cpu_count", lambda: 48)
    _fix_run_returns(monkeypatch, "failed", {"exit": 1, "tail": "boom\n"})
    out = api.bug_fixed(repo, "B1", regression_test=TEST)
    assert out.exit == REFUSED
    assert "under load (load average 192.0 on 48 CPUs)" in out.reason, out.reason
    assert "re-run when it is quieter" in out.reason


def test_a_timeout_is_not_called_a_failure(repo, monkeypatch):
    _case(repo)
    _fix_run_returns(
        monkeypatch,
        "unavailable",
        {"reason": "timed out after 600s", "elapsed_s": 600.0, "timed_out": True},
    )
    out = api.bug_fixed(repo, "B1", regression_test=TEST)
    assert out.exit == OK and out.data["regression_verified"] == "could-not-run", out
    reason = out.data["regression_verify"]["reason"]
    assert "timed out after 600s" in reason and "not a test failure" in reason, reason
    assert "re-run" in reason


def test_the_recorded_evidence_keeps_the_exit_the_output_and_the_load(repo, monkeypatch):
    from test_bug_regression_verified import _fixed_events

    _case(repo)
    _fix_run_returns(
        monkeypatch,
        "unavailable",
        {"reason": "timed out after 5s", "timed_out": True, "exit": 124, "tail": "a\nb\nc\n"},
    )
    out = api.bug_fixed(repo, "B1", regression_test=TEST)
    assert out.exit == OK, out
    (data,) = _fixed_events(repo)
    ev = data["regression_verify"]
    assert ev["fix_exit"] == 124 and ev["fix_output_tail"] == ["a", "b", "c"], ev
    assert "fix_load_1m" in ev and "fix_cpus" in ev


def test_describe_run_cuts_the_output_to_the_configured_lines(monkeypatch):
    from ddflow.config import Config

    cfg = Config.load()
    cfg.lessons.verify_output_lines = 2
    monkeypatch.setattr(os, "getloadavg", lambda: (0.1, 0.1, 0.1))
    why, extra = RD.describe_run("failed", {"exit": 1, "tail": "a\nb\nc\nd\n"}, cfg, where="x")
    assert extra["output_tail"] == ["c", "d"] and "a |" not in why and "c | d" in why
    assert extra["under_load"] is False and "under load" not in why
    cfg.lessons.verify_output_lines = 0
    assert (
        "output_tail"
        not in RD.describe_run("failed", {"exit": 1, "tail": "a\n"}, cfg, where="x")[1]
    )


def test_a_failure_whose_text_says_timed_out_is_not_called_a_timeout():
    from ddflow.config import Config

    why, _ = RD.describe_run(
        "failed",
        {"exit": 1, "reason": "the output matches fail_output /timed out/"},
        Config.load(),
        where="x",
    )
    assert "a timeout, not" not in why and "a timeout is not a verdict" not in why


def test_a_prefix_run_that_could_not_run_keeps_its_evidence(repo, monkeypatch):
    from test_bug_regression_verified import _fixed_events

    _case(repo)
    real = M.run_command_gate
    calls = []

    def fake(gdef, cwd, **kw):
        calls.append(cwd)
        if len(calls) == 2:
            return "unavailable", {
                "reason": "timed out after 5s",
                "timed_out": True,
                "exit": 124,
                "tail": "x\n",
            }
        return real(gdef, cwd, **kw)

    monkeypatch.setattr(M, "run_command_gate", fake)
    out = api.bug_fixed(repo, "B1", regression_test=TEST)
    assert out.exit == OK, out
    ev = _fixed_events(repo)[0]["regression_verify"]
    assert ev["pre_exit"] == 124 and ev["pre_output_tail"] == ["x"] and "pre_load_1m" in ev, ev
