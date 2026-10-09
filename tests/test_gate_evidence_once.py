"""The evidence a gate keeps about its OUTPUT is built once (B-uni-gate-record.3-evidence).

`record --output-file` (api/gates) and a command gate run by ddflow (services/gates/runner)
each built the same four things from a command's output -- its digest, its size, its tail and
the verdict lines from all of it -- by their own copy of the code. `runner.output_evidence`
is the one; both call it, and the oracle below is the pre-change inline code, so the shape and
every value are checked against what the two sites used to write.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ddflow.services import gates as G
from ddflow.services.gates import runner as R

KEYS = ("output_digest", "output_bytes", "tail", "summary")


def oracle(text: str) -> dict:
    """Both old sites, verbatim."""
    return {
        "output_digest": G.digest(text),
        "output_bytes": len(text),
        "tail": text[-2000:],
        "summary": G.summary_lines(text),
    }


TEXTS = [
    "",
    "ok\n",
    "x" * 1999,
    "x" * 2000,
    "x" * 2001 + "\n== 3 passed in 0.1s ==\n",
    "115 failed, 3 passed\n" + "noise\n" * 900 + "112 passed in 4s\n",  # the rerun hides the first
    "héllo \N{SNOWMAN} " + chr(0x200B) + " wide chars " * 300,
    "\n".join(f"line {i}" for i in range(5000)),
]


@pytest.mark.parametrize("text", TEXTS, ids=lambda t: f"{len(t)}b")
def test_the_one_builder_equals_both_old_sites(text: str) -> None:
    assert R.output_evidence(text) == oracle(text)
    assert set(R.output_evidence(text)) == set(KEYS)


def test_the_evidence_is_plain_json() -> None:
    for text in TEXTS:
        ev = R.output_evidence(text)
        assert json.loads(json.dumps(ev)) == ev


def test_a_command_gate_records_exactly_that_evidence(tmp_path: Path) -> None:
    """The runner's evidence for a real command carries the builder's four keys for ITS output."""
    from ddflow.services.gates.defs import GateDef

    gdef = GateDef(id="probe", title="probe", command="printf 'one\\n2 passed in 0.1s\\n'")
    outcome, ev = R.run_command_gate(gdef, tmp_path)
    assert outcome == "passed", ev
    for key, want in R.output_evidence("one\n2 passed in 0.1s\n").items():
        assert ev[key] == want, key


def test_record_with_an_output_file_keeps_the_same_evidence_and_its_own_extras(repo: Path) -> None:
    from conftest import run_cli

    assert run_cli(repo, "init")[0] == 0
    assert run_cli(repo, "phase", "add", "P0", "--title", "p")[0] == 0
    assert run_cli(repo, "task", "add", "P0.T1", "--phase", "P0", "--globs", "a/*")[0] == 0
    assert run_cli(repo, "claim", "P0.T1", "--no-worktree")[0] == 0
    out = repo / "out.txt"
    out.write_text("collecting\n7 passed in 0.2s\n")
    code, _o, err = run_cli(
        repo, "gate", "record", "P0.T1", "unit_tests", "--outcome", "passed",
        "--output-file", str(out), "--command", "pytest", "--exit-code", "0",
    )  # fmt: skip
    assert code == 0, err
    events = [
        json.loads(line)
        for p in (repo / ".ddflow" / "events").glob("*.jsonl")
        for line in p.read_text().splitlines()
    ]
    ev = next(e for e in events if e["kind"] == "gate.passed" and e["subject"] == "P0.T1")["data"]
    ev = ev.get("evidence", ev)
    want = R.output_evidence(out.read_text())
    for key in KEYS:
        assert ev[key] == want[key], key
    assert ev["output_file"] == str(out), "the file's own path is still recorded beside it"


def test_there_is_one_builder_in_the_source() -> None:
    root = Path(__file__).resolve().parents[1] / "ddflow"
    sites = []
    for path in (root / "api" / "gates.py", root / "services" / "gates" / "runner.py"):
        text = path.read_text()
        sites += [(path.name, ln) for ln in text.splitlines() if '"output_digest"' in ln]
    assert len(sites) == 1 and sites[0][0] == "runner.py", sites
