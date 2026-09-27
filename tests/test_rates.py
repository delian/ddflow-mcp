"""B24 + B25: does ddflow's own machinery fire, and how often?

Both checks exist because an unmeasured mechanism is indistinguishable from a missing one.
Both are NOTES rather than problems: a flaky gate and a stalled cadence are defects in the
thing that checks the work, and failing `doctor` on them would block the work itself.
"""

from __future__ import annotations

from conftest import run_cli

from ddflow.config import Config
from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.services import rates as RT


def _log(repo) -> EventLog:
    assert run_cli(repo, "init")[0] == 0
    return EventLog(repo, "a1")


def _state(repo):
    return fold(EventLog(repo, "a1").read_all(), strict=False)


# -- B24: per-gate fire rate ----------------------------------------------------------


def test_a_gate_that_fails_on_everything_is_reported(repo):
    log = _log(repo)
    for i in range(8):
        log.append("gate.failed", f"T{i}", {"gate": "flaky_lint"})
    rates = RT.gate_rates(log.read_all())
    assert rates["flaky_lint"].fail_rate == 1.0
    assert [f.gate for f in RT.failing_gates(rates, Config.load(repo))] == ["flaky_lint"]


def test_a_gate_that_mostly_passes_is_not_reported(repo):
    """A check that fires on a healthy gate is one nobody keeps reading."""
    log = _log(repo)
    for i in range(9):
        log.append("gate.passed", f"T{i}", {"gate": "unit_tests"})
    log.append("gate.failed", "T9", {"gate": "unit_tests"})
    rates = RT.gate_rates(log.read_all())
    assert rates["unit_tests"].fail_rate == 0.1
    assert RT.failing_gates(rates, Config.load(repo)) == []


def test_one_failure_out_of_one_run_is_not_a_finding(repo):
    """100% of one run means nothing, and a new gate's first red must not be a finding."""
    log = _log(repo)
    log.append("gate.failed", "T1", {"gate": "brand_new"})
    rates = RT.gate_rates(log.read_all())
    assert rates["brand_new"].fail_rate == 1.0, "the rate itself is still 100%"
    assert RT.failing_gates(rates, Config.load(repo)) == [], "but it is below rate_min_runs"


def test_a_skip_is_not_a_run(repo):
    """Counting skips as failures would make an unconfigured gate look like a broken one."""
    log = _log(repo)
    for i in range(20):
        log.append("gate.skipped", f"T{i}", {"gate": "never_run", "reason": "not configured"})
    rates = RT.gate_rates(log.read_all())
    assert rates["never_run"].runs == 0
    assert rates["never_run"].skipped == 20
    assert rates["never_run"].fail_rate == 0.0
    assert RT.failing_gates(rates, Config.load(repo)) == []


def test_gate_started_and_out_of_order_are_not_verdicts(repo):
    """`gate.started` is not an outcome, and `gate.out_of_order` is a complaint about the
    pipeline rather than a verdict about the work — counting either would distort the rate."""
    log = _log(repo)
    for i in range(6):
        log.append("gate.started", f"T{i}", {"gate": "g"})
        log.append("gate.passed", f"T{i}", {"gate": "g"})
    log.append("gate.out_of_order", "T7", {"gate": "g"})
    rates = RT.gate_rates(log.read_all())
    assert rates["g"].runs == 6, rates["g"].outcomes
    assert rates["g"].fail_rate == 0.0


def test_the_gate_rate_knobs_are_read(repo):
    """The dead-knob class: both thresholds must change the verdict."""
    log = _log(repo)
    for i in range(6):
        log.append("gate.failed", f"T{i}", {"gate": "g"})
    rates = RT.gate_rates(log.read_all())
    cfg = Config.load(repo)
    assert RT.failing_gates(rates, cfg), "6 failures of 6 should be reported by default"
    cfg.gates.rate_min_runs = 100
    assert RT.failing_gates(rates, cfg) == [], "rate_min_runs did not raise the bar"
    cfg.gates.rate_min_runs = 1
    cfg.gates.rate_max_fail = 1.01
    assert RT.failing_gates(rates, cfg) == [], "rate_max_fail did not raise the bar"


# -- B25: cadence fired versus scheduled ----------------------------------------------


def _complete_tasks(log, n: int) -> None:
    for i in range(n):
        log.append("task.added", f"C{i}", {"title": "t", "kind": "task"})
        log.append("item.completed", f"C{i}", {})


def test_a_cadence_that_never_fired_is_reported(repo):
    """The source project's failure: scheduled three times, fired zero, nothing said so."""
    log = _log(repo)
    _complete_tasks(log, 12)
    cfg = Config.load(repo)
    rates = {r.name: r for r in RT.cadence_rates(_state(repo), cfg)}
    # every 5 tasks -> 2 expected; every 4 -> 3 expected
    assert (rates["integration_tests"].expected, rates["integration_tests"].ran) == (2, 0)
    assert (rates["dedupe_sweep"].expected, rates["dedupe_sweep"].ran) == (3, 0)
    assert sorted(r.name for r in RT.never_fired(_state(repo), cfg)) == [
        "dedupe_sweep",
        "integration_tests",
    ]


def test_a_cadence_that_keeps_up_is_not_reported(repo):
    log = _log(repo)
    _complete_tasks(log, 12)
    for _ in range(3):
        log.append("cadence.ran", "integration_tests", {"result": "12"})
        log.append("cadence.ran", "dedupe_sweep", {"result": "12"})
    assert RT.never_fired(_state(repo), Config.load(repo)) == []


def test_running_early_is_not_a_defect(repo):
    """`missed` must not go negative and must not become a finding: a project that runs a
    pass more often than required is doing the right thing."""
    log = _log(repo)
    _complete_tasks(log, 5)
    for _ in range(9):
        log.append("cadence.ran", "integration_tests", {"result": "5"})
    r = next(
        x
        for x in RT.cadence_rates(_state(repo), Config.load(repo))
        if x.name == "integration_tests"
    )
    assert r.ran == 9 and r.expected == 1
    assert r.missed == 0, "running early must not read as missing"
    assert RT.never_fired(_state(repo), Config.load(repo)) == []


def test_being_merely_due_is_not_a_finding(repo):
    """`ddflow cadence` already reports due-ness. This check is for never firing, and a
    threshold of one period is what keeps the two from saying the same thing."""
    log = _log(repo)
    _complete_tasks(log, 5)  # exactly one integration_tests period
    assert RT.never_fired(_state(repo), Config.load(repo)) == []


def test_the_max_missed_knob_is_read(repo):
    log = _log(repo)
    _complete_tasks(log, 12)
    cfg = Config.load(repo)
    assert RT.never_fired(_state(repo), cfg), "2 missed should be reported at the default of 1"
    cfg.cadence.max_missed = 99
    assert RT.never_fired(_state(repo), cfg) == [], "max_missed did not raise the bar"


def test_a_cadence_with_every_zero_is_not_a_division_error(repo):
    """`every = 0` means "disabled", and it must not raise or claim infinite expectations."""
    log = _log(repo)
    _complete_tasks(log, 12)
    cfg = Config.load(repo)
    cfg.cadence.integration_tests_every_tasks = 0
    r = next(x for x in RT.cadence_rates(_state(repo), cfg) if x.name == "integration_tests")
    assert r.expected == 0 and r.missed == 0


# -- both, on the surface an operator actually reads ----------------------------------


def test_doctor_reports_both_as_notes_not_problems(repo):
    """A defect in the checking machinery must not block the work being checked."""
    log = _log(repo)
    for i in range(8):
        log.append("gate.failed", f"T{i}", {"gate": "flaky_lint"})
    _complete_tasks(log, 12)
    rc, out, _err = run_cli(repo, "doctor")
    assert "flaky_lint" in out, out
    assert "integration_tests" in out, out
    assert rc == 0, f"a flaky gate and a stalled cadence must not fail doctor:\n{out}"
