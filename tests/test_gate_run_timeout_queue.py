"""B83c7a5eb50: the wrapping `gate run ... ci` timeout counts the time `ddflow ci run` spends
queued for the host suite lock, so a run that queued near its limit was killed (3600s) before
it ever started, and `[ci].suite_wait_s`/`DDFLOW_CI_SUITE_WAIT_S` was unreachable past the gate's
own timeout. `ddflow ci run` separates its queue wait from its run budget itself (B4f35548c0a),
so the gate that wraps it must add `[ci].suite_wait_s` to the run budget it enforces: the queue
time never comes off the run's budget."""

from __future__ import annotations

import pytest

from ddflow.config import Config
from ddflow.services import cmdrunner
from ddflow.services.gates import runner as R
from ddflow.services.gates.defs import GateDef


def _captured_timeout(tmp_path, monkeypatch, *, gate: GateDef, suite_wait_s: int) -> float:
    cfg = Config()
    cfg.ci.suite_wait_s = suite_wait_s
    seen: dict[str, float] = {}

    def fake_run(self, declared, **kw):
        seen["timeout_s"] = kw["timeout_s"]
        return cmdrunner.CommandRun("ran", declared.line, code=0, elapsed_s=0.0)

    monkeypatch.setattr(cmdrunner.CommandRunner, "run", fake_run)
    outcome, _ = R.run_command_gate(gate, tmp_path, cfg=cfg)
    assert outcome == "passed"
    return seen["timeout_s"]


def test_the_ci_gate_timeout_budget_does_not_count_the_suite_queue_wait(tmp_path, monkeypatch):
    gate = GateDef(id="ci", title="ci", command="ddflow ci run", timeout_s=3600)
    # 3600s run budget + the 900s queue wait the run may spend queued before it starts: the
    # queue time is separate from the budget, so the outer timeout must cover both.
    assert _captured_timeout(tmp_path, monkeypatch, gate=gate, suite_wait_s=900) == 4500


def test_a_queue_wait_is_added_to_whatever_the_run_budget_is(tmp_path, monkeypatch):
    gate = GateDef(id="ci", title="ci", command="ddflow ci run", timeout_s=120)
    assert _captured_timeout(tmp_path, monkeypatch, gate=gate, suite_wait_s=900) == 1020


def test_an_unlimited_ci_gate_timeout_stays_unlimited(tmp_path, monkeypatch):
    """The queue wait only extends a FINITE budget; a `0` (no timeout) must not become one."""
    gate = GateDef(id="ci", title="ci", command="ddflow ci run", timeout_s=0)
    assert _captured_timeout(tmp_path, monkeypatch, gate=gate, suite_wait_s=900) == 0


def test_a_ci_command_that_does_not_queue_internally_is_not_extended(tmp_path, monkeypatch):
    """Only a `ddflow ci run` command queues for the suite lock inside itself; a custom ci
    command runs straight through, so its budget is untouched."""
    gate = GateDef(id="ci", title="ci", command="pytest", timeout_s=3600)
    assert _captured_timeout(tmp_path, monkeypatch, gate=gate, suite_wait_s=900) == 3600


def test_the_overridden_ci_run_timeout_is_extended_too(tmp_path, monkeypatch):
    """`gate run ... ci --timeout N` rewrites the command to `ddflow ci run --timeout N`; the
    wrapping budget is still the run budget plus the queue wait, not only N."""
    gate = GateDef(id="ci", title="ci", command="ddflow ci run --timeout 90", timeout_s=90)
    assert _captured_timeout(tmp_path, monkeypatch, gate=gate, suite_wait_s=900) == 990


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__]))
