"""B-gate-status-hides-failed-reason: `ddflow gate status` dropped a FAILED gate's reason.

A failed ci that had also queued for minutes rendered `[!] ci -- queued 48m of 81m elapsed`:
`_why_notes` only surfaced the recorded reason for `unavailable`/`partial`, so a FAILED gate's
reason lost to the queued-elapsed note and a real new test failure read as a queue timeout.
The reason is why the gate has no passing verdict and must win over the queue note.
"""

from __future__ import annotations

import json
from types import SimpleNamespace as NS

from conftest import run_cli


def test_gate_status_says_why_a_gate_failed(repo):
    run_cli(repo, "init")
    (repo / ".ddflow" / "config.toml").write_text(
        '[gates]\ntask_pipeline = ["implement", "ci", "merge"]\n'
    )
    run_cli(repo, "phase", "add", "P1", "--title", "P1 fixture", "--globs", "src/**")
    run_cli(
        repo, "task", "add", "T1", "--title", "T1 fixture", "--phase", "P1", "--globs", "src/x.py"
    )
    run_cli(repo, "claim", "T1")
    code, _out, err = run_cli(
        repo,
        "gate",
        "record",
        "T1",
        "ci",
        "--outcome",
        "failed",
        "--reason",
        "1 test failed: tests/test_x.py",
    )
    assert code == 0, err
    _code, out, _ = run_cli(repo, "gate", "status", "T1")
    row = next(x for x in out.splitlines() if "] ci" in x)
    assert "1 test failed: tests/test_x.py" in row, out
    _code, out, _ = run_cli(repo, "--json", "gate", "status", "T1")
    assert json.loads(out)["why"]["ci"] == "1 test failed: tests/test_x.py"


def test_a_failed_gates_reason_wins_over_the_queued_note():
    from ddflow.services.gates.status_notes import _why_notes

    it = NS(
        gates={
            "ci": NS(
                reason="1 test failed: tests/test_x.py",
                evidence={"waited_s": 2880, "elapsed_s": 4860},
            )
        }
    )
    assert _why_notes(it, [("ci", "failed")]) == {"ci": "1 test failed: tests/test_x.py"}


def test_a_passed_gate_still_shows_its_queued_note():
    from ddflow.services.gates.status_notes import _why_notes

    it = NS(gates={"ci": NS(reason="", evidence={"waited_s": 2880, "elapsed_s": 4860})})
    assert _why_notes(it, [("ci", "passed")]) == {"ci": "queued 48m of 81m elapsed"}
