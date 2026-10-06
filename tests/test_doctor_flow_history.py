"""doctor names each log-derived flow signal that has too little history (bug Bc6784dab3c).

`flowsignals.history_notes` was written for doctor and documented in the README ("a fresh
project simply reports the first four as unavailable until it has history"), but nothing
called it: an operator was never told that a log signal was inert.
"""

from __future__ import annotations

from conftest import run_cli


def test_doctor_notes_the_log_signals_a_fresh_project_cannot_read_yet(repo):
    assert run_cli(repo, "init")[0] == 0
    rc, out, _err = run_cli(repo, "doctor")
    for name in ("reviewer_latency_ratio", "gate_failure_rate", "merge_failure_rate"):
        assert f"{name}: too little history in the log yet" in out, out
    assert rc == 0, f"too little history is a note, never a failure:\n{out}"


def test_fixed_parallelism_reads_no_signals_and_notes_none(repo):
    assert run_cli(repo, "init")[0] == 0
    assert run_cli(repo, "config", "schedule.parallel", "fixed")[0] == 0
    rc, out, _err = run_cli(repo, "doctor")
    assert rc == 0, out  # doctor ran: "no note" must come from a doctor that looked
    assert "too little history" not in out, out


def test_a_switched_off_signal_is_not_noted(repo):
    assert run_cli(repo, "init")[0] == 0
    on = '["load_per_core", "gate_failure_rate", "merge_failure_rate"]'
    assert run_cli(repo, "config", "--set", "schedule.signals.enabled", on)[0] == 0
    rc, out, _err = run_cli(repo, "doctor")
    assert rc == 0, out
    assert "reviewer_latency_ratio: too little" not in out, out
    assert "gate_failure_rate: too little history" in out, out


def test_the_ratio_is_noted_by_itself_when_the_rate_is_switched_off():
    from ddflow.core import flowsignals as FS

    empty = FS.Signals()
    assert FS.history_notes(empty, ["gate_failure_ratio"]) == [
        "gate_failure_ratio: too little history in the log yet (neutral, not used)"
    ]
    # with the rate on, its note covers the short hour and the ratio adds nothing
    assert [
        n.split(":")[0]
        for n in FS.history_notes(empty, ["gate_failure_rate", "gate_failure_ratio"])
    ] == ["gate_failure_rate"]
