"""An evidence-required gate refuses a bare pass, on every surface.

Found while adding the docs phase gate: api/gates.record filled the evidence with fields
ddflow MEASURES itself (tree_sha, diff_stat) before the service checked for evidence, so
the check never fired and a bare 'passed' was accepted for unit_tests, rubber_duck,
critic, standards and docs alike (bug Bbc9a7ee3f2). "If the only thing standing between
'I ran the tests' and a recorded pass is the agent's honesty, the record measures
honesty rather than testing" -- the service docstring's own words.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli


def _task(repo: Path) -> None:
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", "a.py")


def test_a_bare_pass_on_an_evidence_required_gate_is_refused(repo):
    _task(repo)
    code, _out, err = run_cli(repo, "gate", "record", "T1", "rubber_duck", "--outcome", "passed")
    assert code != 0, "a bare pass was recorded"
    assert "requires evidence" in err


def test_a_pass_with_evidence_is_recorded_with_the_measured_fields_too(repo):
    _task(repo)
    code, _out, err = run_cli(
        repo, "gate", "record", "T1", "rubber_duck", "--outcome", "passed",
        "--evidence", "reviewed by X: 2 findings fixed",
    )  # fmt: skip
    assert code == 0, err
    events = [
        json.loads(line)
        for shard in (repo / ".ddflow" / "events").glob("*.jsonl")
        for line in shard.read_text().splitlines()
    ]
    rec = [e for e in events if e["kind"] == "gate.passed" and e["data"]["gate"] == "rubber_duck"]
    assert rec, "not recorded"
    ev = rec[-1]["data"]["evidence"]
    assert ev["note"] == "reviewed by X: 2 findings fixed"
    assert "tree_sha" in ev and "diff_stat" in ev, "the measured fields are still recorded"


def test_a_gate_that_does_not_require_evidence_still_passes_bare(repo):
    _task(repo)
    code, _out, err = run_cli(repo, "gate", "record", "T1", "implement", "--outcome", "passed")
    assert code == 0, err
