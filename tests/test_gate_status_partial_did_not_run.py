"""B-gate-status-partial-did-not-run: `gate status` said a PARTIAL gate "did not run".

A gate recorded `partial` RAN, and carried counts (CI: 16 failed, 572 passed). `gate status`
lumped `partial` into its "did not run. That is a gap in coverage" note, so an already-run
suite read as one that never ran. A partial gate is separated from the never-ran ones and
shows its recorded coverage -- a reviewer's ``coverage``, else the runner's own counts from
its summary lines (rather than "unknown").
"""

from __future__ import annotations

from conftest import run_cli

OK, FAIL, NOTHING, REFUSED = 0, 1, 2, 3


def _pipeline(repo, pipeline="implement,ci,merge"):
    run_cli(repo, "init")
    run_cli(repo, "workflow", "pipeline", "task", pipeline)
    run_cli(repo, "task", "add", "T1", "--title", "T1 fixture", "--globs", "a.py")
    run_cli(repo, "claim", "T1", "--no-worktree")


def _record_ok(repo, *argv: str) -> tuple[int, str, str]:
    code, out, err = run_cli(repo, "gate", "record", *argv)
    assert code == OK, err
    return code, out, err


def test_a_partial_gate_is_not_said_to_have_not_run(repo):
    _pipeline(repo)
    _record_ok(repo, "T1", "implement", "--outcome", "passed")
    _record_ok(repo, "T1", "ci", "--outcome", "partial", "--reason", "16 failed, 572 passed")
    _record_ok(repo, "T1", "merge", "--outcome", "passed")
    _code, out, _ = run_cli(repo, "gate", "status", "T1")
    # The reported note was a comma-joined "NOTE: ci, unit_tests did not run": the partial
    # gate must not be a member of the never-ran list, whatever gates surround it.
    never = [ln for ln in out.splitlines() if "NOTE:" in ln and "did not run" in ln]
    assert not any("ci" in ln for ln in never), out
    assert "ci ran only partially" in out, out


def test_a_partial_gates_counts_come_from_its_summary(repo):
    """A partial gate with no `coverage` shows its runner's own counts, not "unknown"."""
    from ddflow.infra.log import EventLog

    _pipeline(repo)
    run_cli(repo, "gate", "record", "T1", "implement", "--outcome", "passed")
    log = EventLog(repo, "ddflow-gate-run")
    log.append(
        "gate.partial",
        "T1",
        {
            "gate": "ci",
            "by": "ddflow",
            "reason": "ran to the end with failures",
            "evidence": {"summary": ["16 failed, 572 passed in 5.2s"], "exit": 1},
        },
    )
    _code, out, _ = run_cli(repo, "gate", "status", "T1")
    assert "ci did not run" not in out, out
    assert "ci ran only partially (coverage: 16 failed, 572 passed)" in out, out


def test_an_unavailable_gate_is_still_said_to_have_not_run(repo):
    _pipeline(repo)
    run_cli(repo, "gate", "record", "T1", "implement", "--outcome", "passed")
    run_cli(repo, "gate", "record", "T1", "ci", "--outcome", "unavailable", "--reason", "down")
    _code, out, _ = run_cli(repo, "gate", "status", "T1")
    assert "ci did not run" in out, out
    assert "ran only partially" not in out, out
