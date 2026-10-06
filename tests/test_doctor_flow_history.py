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
    _rc, out, _err = run_cli(repo, "doctor")
    assert "too little history" not in out, out
