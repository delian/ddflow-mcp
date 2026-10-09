"""The evidence a gate keeps about its OUTPUT is built once (B-uni-gate-record.3-evidence).

`record --output-file` (api/gates) and a command gate run by ddflow (services/gates/runner)
each built the same four things from a command's output -- its digest, its size, its tail and
the verdict lines from all of it -- by their own copy of the code. `runner.output_evidence`
is the one; both call it, and the oracle below is the pre-change inline code, so the shape and
every value are checked against what the two sites used to write.
"""

from __future__ import annotations

import ast
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


def test_the_values_themselves_are_pinned_not_only_their_agreement() -> None:
    """Literal expectations, so a change to `digest` or `summary_lines` shows here too."""
    assert R.output_evidence("ok\n") == {
        "output_digest": "ff0f972446b0b858",
        "output_bytes": 3,
        "tail": "ok\n",
        "summary": [],
    }
    assert R.output_evidence("115 failed, 3 passed\nnoise\n112 passed in 4s\n") == {
        "output_digest": "6bab880599228cce",
        "output_bytes": 44,
        "tail": "115 failed, 3 passed\nnoise\n112 passed in 4s\n",
        "summary": ["112 passed in 4s"],
    }
    assert R.OUTPUT_TAIL_CHARS == 2000


def _writes_in(source: str) -> list[int]:
    """The lines of ``source`` that SET ``"output_bytes"`` -- the size of a command's output,
    which only the evidence of that output carries (a review transcript's digest is another
    thing and has no size; reading the key back, as `verify` does, is not building it). Three
    forms: a dict literal key, a subscript assignment and a keyword (``dict(output_bytes=...)``)."""
    lines = []
    for node in ast.walk(ast.parse(source)):
        literal = isinstance(node, ast.Dict) and any(
            isinstance(k, ast.Constant) and k.value == "output_bytes" for k in node.keys
        )
        stored = (
            isinstance(node, ast.Subscript)
            and isinstance(node.ctx, ast.Store)
            and isinstance(node.slice, ast.Constant)
            and node.slice.value == "output_bytes"
        )
        keyword = isinstance(node, ast.keyword) and node.arg == "output_bytes"
        if literal or stored or keyword:
            lines.append(getattr(node, "lineno", 0))
    return lines


def _writes_of_output_bytes() -> list[str]:
    root = Path(__file__).resolve().parents[1] / "ddflow"
    return [
        f"{path.relative_to(root)}:{line}"
        for path in sorted(root.rglob("*.py"))
        for line in _writes_in(path.read_text("utf-8"))
    ]


def test_the_scan_sees_each_way_of_building_the_evidence() -> None:
    """The structural check below must not be blind to a copy written another way."""
    assert _writes_in('ev = {"output_bytes": 1}') == [1]
    assert _writes_in('ev = {}\nev["output_bytes"] = 1') == [2]
    assert _writes_in("ev = dict(output_bytes=1)") == [1]
    assert _writes_in('size = ev["output_bytes"]\nrun.output_bytes = 3') == [], (
        "reading is not building"
    )


def test_there_is_one_builder_in_the_source() -> None:
    sites = _writes_of_output_bytes()
    assert len(sites) == 1 and sites[0].startswith("services/gates/runner.py"), sites
