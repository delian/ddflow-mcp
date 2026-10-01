"""A command gate's evidence keeps what it digests (bug Bac392907b1).

`gate run` recorded `output_digest` over the whole output but kept only its last
2,000 bytes, so the digest named bytes nobody kept and could never be checked -- and a
gate that reruns its failures (a parallel pass, then a serial rerun of the failures)
showed only the rerun's "112 passed", hiding that the first pass had 115 failures.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.services import gates as G

OK = 0

#: A first pass that fails, a long stretch of noise, then a rerun that passes.
SCRIPT = (
    "print('=========== 115 failed, 3 passed in 9.10s ==========='); "
    "print('x' * 80 + chr(10)) ; "
    "[print('noise line', i) for i in range(400)]; "
    "print('=========== 112 passed in 1.02s ===========')"
)


def _evidence(repo: Path, gate: str = "unit_tests") -> dict:
    st = fold(EventLog(repo).read_all(), strict=False)
    return st.items["T1"].gates[gate].evidence


def _setup(repo: Path) -> None:
    run_cli(repo, "init")
    cmd = f'{sys.executable} -c "{SCRIPT}"'
    (repo / ".ddflow" / "gates.toml").write_text(
        f'[gate.unit_tests]\ncommand = {json.dumps(cmd)}\ncwd = "repo"\n'
    )
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")


def test_gate_run_keeps_the_whole_output_its_digest_names(repo):
    _setup(repo)
    code, out, err = run_cli(repo, "gate", "run", "T1", "unit_tests")
    assert code == OK, out + err
    ev = _evidence(repo)
    log = repo / ev["output_log"]
    assert log.is_file(), ev
    text = log.read_text("utf-8")
    assert G.digest(text) == ev["output_digest"], "the kept output is not what was digested"
    assert len(text) == ev["output_bytes"]
    assert "115 failed" in text and "115 failed" not in ev["tail"]


def test_gate_run_lifts_the_suites_summary_lines_from_the_whole_output(repo):
    _setup(repo)
    assert run_cli(repo, "gate", "run", "T1", "unit_tests")[0] == OK
    summary = _evidence(repo)["summary"]
    assert any("115 failed" in line for line in summary), summary
    assert any("112 passed" in line for line in summary), summary


def test_the_run_logs_are_never_committed(repo):
    _setup(repo)
    assert run_cli(repo, "gate", "run", "T1", "unit_tests")[0] == OK
    log = _evidence(repo)["output_log"]
    r = subprocess.run(
        ["git", "-C", str(repo), "check-ignore", "-q", log], capture_output=True, check=False
    )
    assert r.returncode == 0, f"{log} is not git-ignored"


def test_gate_record_keeps_the_output_file_it_digests(repo, tmp_path):
    _setup(repo)
    out_file = tmp_path / "run.log"
    out_file.write_text("=== 2 failed ===\n" + "y" * 3000 + "\n=== 9 passed ===\n")
    code, out, err = run_cli(
        repo, "gate", "record", "T1", "unit_tests", "--outcome", "passed",
        "--command", "pytest", "--exit-code", "0", "--evidence", "ran it",
        "--output-file", str(out_file),
    )  # fmt: skip
    assert code == OK, out + err
    ev = _evidence(repo)
    assert ev["output_file"] == str(out_file)
    assert any("2 failed" in line for line in ev["summary"]), ev


def test_summary_lines_read_pytest_quiet_and_unittest_verdicts():
    out = (
        "collected 9\n"
        "3 failed, 112 passed, 18 warnings in 91.95s (0:01:31)\n"
        "noise\n"
        "Ran 3 tests in 0.001s\n"
        "FAILED (failures=1)\n"
        "12 passed items, not a verdict\n"
    )
    assert G.summary_lines(out) == [
        "3 failed, 112 passed, 18 warnings in 91.95s (0:01:31)",
        "Ran 3 tests in 0.001s",
        "FAILED (failures=1)",
    ]


def test_run_logs_are_pruned_per_gate_and_never_the_one_just_written(tmp_path):
    keep_unit = G.run_log_writer(tmp_path, "T1", "unit")
    keep_fast = G.run_log_writer(tmp_path, "T1", "unit-fast")
    fast = [keep_fast(f"fast {i}") for i in range(3)]
    unit = [keep_unit(f"unit {i}") for i in range(G.KEEP_RUN_LOGS + 5)]
    for ref in fast:
        assert (tmp_path / ref).is_file(), "another gate's logs were pruned"
    assert (tmp_path / unit[-1]).read_text() == f"unit {G.KEEP_RUN_LOGS + 4}"
    left = sorted(p.name for p in (tmp_path / ".ddflow" / "runs" / "T1").glob("unit-2*.log"))
    assert len(left) == G.KEEP_RUN_LOGS
    assert [(tmp_path / ".ddflow" / "runs" / "T1" / n).read_text() for n in left] == [
        f"unit {i}" for i in range(5, G.KEEP_RUN_LOGS + 5)
    ]
