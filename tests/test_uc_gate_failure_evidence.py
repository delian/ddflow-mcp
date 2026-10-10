"""A failed gate keeps WHICH tests failed and why; a test that failed and then passed is logged.

Operator 2026-10-10 (B-uc-gate-failure-evidence): one-off failures under load passed on the
rerun, and the ci / unit_tests evidence kept only a summary line, so nobody could see which
test failed. Pinned here: the failing ids and failure tails reach the evidence and the text a
person reads; a test that fails and then passes on the same tree lands in the machine-local flake log
(`ddflow tests --flakes`, `doctor` at 3); `gate run --rerun-failed` runs ONLY the failed tests
once and records both outcomes while the gate stays FAILED (D-failed-gate-rerun).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.services import flakes as FK
from ddflow.services.gates import runner as R

# Built at run time: a literal key-shaped string in a source file trips the secret scan.
FAKE_KEY = "sk-" + "abcdef0123456789" + "abcdef"

PYTEST_OUT = """\
============================= test session starts ==============================
..F.E
=================================== FAILURES ===================================
__________________________________ test_a ______________________________________

    def test_a():
>       assert 1 == 2
E       assert 1 == 2

tests/test_x.py:3: AssertionError
_______________________ TestC.test_b[x-1] _______________________

    boom
E   ValueError: nope
=========================== short test summary info ============================
FAILED tests/test_x.py::test_a - assert 1 == 2
FAILED tests/test_x.py::TestC::test_b[x-1] - ValueError: nope
ERROR tests/test_y.py::test_c - fixture 'q' not found
=================== 2 failed, 1 error, 7 passed in 0.31s =======================
"""


def test_the_failing_ids_and_reasons_are_read_from_pytest_output() -> None:
    found = FK.failed_tests(PYTEST_OUT)
    assert [f["id"] for f in found] == [
        "tests/test_x.py::test_a",
        "tests/test_x.py::TestC::test_b[x-1]",
        "tests/test_y.py::test_c",
    ]
    assert found[1]["why"] == "ValueError: nope"


def test_each_failure_section_keeps_its_last_lines() -> None:
    tails = {t["test"]: t["tail"] for t in FK.failure_tails(PYTEST_OUT)}
    assert set(tails) == {"test_a", "TestC.test_b[x-1]"}
    assert "AssertionError" in tails["test_a"] and "session starts" not in tails["test_a"]
    assert tails["TestC.test_b[x-1]"].endswith("ValueError: nope")


def test_unittest_headings_are_read_too() -> None:
    out = "FAIL: test_m (pkg.mod.Case.test_m)\nERROR: test_n (pkg.mod.Case)\nFAILED (failures=1)\n"
    assert [f["id"] for f in FK.failed_tests(out)] == ["pkg.mod.Case.test_m", "pkg.mod.Case.test_n"]


def test_the_evidence_is_capped() -> None:
    out = "\n".join(f"FAILED tests/t.py::test_{i} - x" for i in range(80))
    ev = FK.failure_evidence(out)
    assert len(ev["failed_tests"]) == FK.MAX_FAILED_TESTS and ev["failed_tests_more"] == 30
    assert FK.failure_evidence("3 passed in 0.1s\n") == {}


def test_output_evidence_carries_the_failing_tests() -> None:
    ev = R.output_evidence(PYTEST_OUT)
    assert ev["failed_tests"][0] == "tests/test_x.py::test_a"
    assert "2 failed, 1 error, 7 passed" in ev["summary"][-1]
    assert R.output_evidence("2 passed in 0.1s\n").keys() == {
        "output_digest", "output_bytes", "tail", "summary",
    }  # fmt: skip


def _fake_pytest(repo: Path, state: Path, fails: int) -> None:
    """A `pytest` that fails its first ``fails`` runs and passes after (state lives outside
    the repo so the tree does not change between runs)."""
    (repo / "bin").mkdir()
    (repo / "tests").mkdir()
    script = repo / "bin" / "pytest"
    script.write_text(
        "#!/bin/sh\n"
        f"n=$(cat {state} 2>/dev/null || echo 0); echo $((n+1)) > {state}\n"
        f'if [ "$n" -lt {fails} ]; then\n'
        "  printf '____ test_a ____\\nE assert 1 == 2\\n=== short test summary info ===\\n"
        "FAILED tests/test_x.py::test_a - assert 1 == 2\\n1 failed in 0.1s\\n'; exit 1\n"
        "fi\n"
        'echo "ran: $*"; echo "1 passed in 0.1s"\n'
    )
    script.chmod(0o755)


def _project(repo: Path, tmp_path: Path, fails: int) -> Path:
    state = tmp_path / "calls"
    run_cli(repo, "init")
    _fake_pytest(repo, state, fails)
    (repo / ".ddflow" / "gates.toml").write_text(
        '[gate.unit_tests]\ncommand = "./bin/pytest -q tests/"\ncwd = "repo"\n'
    )
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")
    run_cli(repo, "claim", "T1", "--no-worktree", agent="worker")
    return state


def test_a_failing_gate_names_the_test_in_its_evidence_and_its_text(repo, tmp_path) -> None:
    _project(repo, tmp_path, fails=1)
    code, out, err = run_cli(repo, "gate", "run", "T1", "unit_tests", agent="worker")
    assert code == 1
    assert "FAILED tests/test_x.py::test_a - assert 1 == 2" in out + err
    st = fold(EventLog(repo).read_all(), strict=False)
    ev = st.items["T1"].gates["unit_tests"].evidence
    assert ev["failed_tests"] == ["tests/test_x.py::test_a"]
    assert ev["failed_reasons"] == {"tests/test_x.py::test_a": "assert 1 == 2"}
    assert ev["failure_tails"][0]["test"] == "test_a"


def test_rerun_failed_runs_only_the_failed_tests_and_the_gate_stays_failed(repo, tmp_path) -> None:
    _project(repo, tmp_path, fails=1)
    code, out, _err = run_cli(
        repo, "--json", "gate", "run", "T1", "unit_tests", "--rerun-failed", agent="worker"
    )
    assert code == 1, "the gate failed; a passing rerun does not override it"
    body = json.loads(out)
    assert body["outcome"] == "failed"
    ev = body["evidence"]
    assert ev["failed_tests"] == ["tests/test_x.py::test_a"], "the first failure stays"
    rr = ev["rerun"]
    assert rr["outcome"] == "passed" and rr["tests"] == ["tests/test_x.py::test_a"]
    assert "tests/test_x.py::test_a" in rr["command"] and "tests/ " not in rr["command"]
    assert "stays FAILED" in rr["note"]
    st = fold(EventLog(repo).read_all(), strict=False)
    assert st.items["T1"].gates["unit_tests"].outcome == "failed"
    rows = FK.read(repo)
    assert [r["test"] for r in rows] == ["tests/test_x.py::test_a"]
    assert rows[0]["how"] == "the failed tests passed on a rerun"
    assert rows[0]["failure"] == "assert 1 == 2" and rows[0]["commit"]
    _c, shown, _e = run_cli(repo, "tests", "--flakes")
    assert "1x  tests/test_x.py::test_a" in shown and "assert 1 == 2" in shown


def test_a_gate_rerun_that_passes_on_the_same_tree_logs_the_flake(repo, tmp_path) -> None:
    _project(repo, tmp_path, fails=1)
    assert run_cli(repo, "gate", "run", "T1", "unit_tests", agent="worker")[0] == 1
    assert FK.read(repo) == [], "a failure alone is not a flake"
    code, _o, _e = run_cli(repo, "gate", "run", "T1", "unit_tests", agent="worker")
    assert code == 0
    rows = FK.read(repo)
    assert [r["test"] for r in rows] == ["tests/test_x.py::test_a"]
    assert rows[0]["how"] == "the gate re-run passed on the same tree"
    st = fold(EventLog(repo).read_all(), strict=False)
    assert [g.outcome for g in st.items["T1"].gate_history if g.gate == "unit_tests"] == [
        "failed",
        "passed",
    ], "the first failure stays in the history"


def test_a_gate_that_passes_first_time_logs_nothing(repo, tmp_path) -> None:
    _project(repo, tmp_path, fails=0)
    assert (
        run_cli(repo, "gate", "run", "T1", "unit_tests", "--rerun-failed", agent="worker")[0] == 0
    )
    assert FK.read(repo) == []
    assert "No flakes logged" in run_cli(repo, "tests", "--flakes")[1]


def test_a_test_that_flakes_three_times_is_named_by_doctor(repo) -> None:
    assert FK.doctor_notes(repo) == []
    for _ in range(FK.CHRONIC_FAILS):
        FK.record(repo, ["tests/t.py::test_slow"], item="T", gate="unit_tests", commit="abc")
    (note,) = FK.doctor_notes(repo)
    assert "tests/t.py::test_slow" in note and "3 times" in note
    _c, out, _e = run_cli(repo, "doctor")
    assert "tests/t.py::test_slow" in out


def test_the_flake_log_is_local_and_never_committed(repo) -> None:
    run_cli(repo, "init")
    FK.record(repo, ["tests/t.py::test_x"], item="T", gate="unit_tests", commit="abc")
    import subprocess

    ignored = subprocess.run(
        ["git", "-C", str(repo), "check-ignore", "-q", str(FK.FLAKE_LOG)]
    ).returncode
    assert ignored == 0 and os.path.exists(FK.log_path(repo))


def test_ci_run_names_the_failing_tests_from_all_of_its_output(repo) -> None:
    run_cli(repo, "init")
    cmd = (  # the FAILED line is followed by 3000 lines: a tail of lines or bytes loses it
        "printf '____ test_a ____\\nE boom\\nFAILED tests/test_x.py::test_a - boom\\n'; "
        "i=0; while [ $i -lt 3000 ]; do echo noise-noise-noise-noise-$i; i=$((i+1)); done; exit 1"
    )
    code, out, err = run_cli(repo, "--json", "ci", "run", "--command", cmd)
    assert code == 1
    data = json.loads(out)
    assert data["failed_tests"] == ["tests/test_x.py::test_a"], "the tail alone would hide it"
    assert "FAILED tests/test_x.py::test_a - boom" in data["failure_text"]
    code, out, err = run_cli(repo, "ci", "run", "--command", cmd)
    assert "FAILED tests/test_x.py::test_a - boom" in err


def test_a_parameter_id_with_spaces_is_one_id() -> None:
    out = "FAILED tests/t.py::test_y[hello world] - AssertionError: 1 != 2\nFAILED tests/t.py::test_z\n"
    got = {f["id"]: f["why"] for f in FK.failed_tests(out)}
    assert got == {
        "tests/t.py::test_y[hello world]": "AssertionError: 1 != 2",
        "tests/t.py::test_z": "",
    }


def test_a_flake_row_takes_the_section_that_names_exactly_its_test(repo) -> None:
    run_cli(repo, "init")
    tails = [
        {"test": "test_review_budget", "tail": "short one"},
        {"test": "TestC.test_review_budget_extra", "tail": "long one"},
    ]
    FK.record(
        repo,
        ["tests/t.py::test_review_budget_extra", "tests/t.py::TestC::test_review_budget_extra"],
        tails=tails,
    )
    assert [r["failure"] for r in FK.read(repo)] == ["", "long one"]


def test_the_flake_log_masks_secrets(repo) -> None:
    run_cli(repo, "init")
    FK.record(repo, ["tests/t.py::test_a"], why={"tests/t.py::test_a": f"token={FAKE_KEY}"})
    (row,) = FK.read(repo)
    assert FAKE_KEY not in row["failure"]


def test_the_flake_log_keeps_the_newest_rows(repo) -> None:
    run_cli(repo, "init")
    for i in range(2 * FK.MAX_ROWS + 1):
        FK.record(repo, [f"tests/t.py::test_{i}"])
    rows = FK.read(repo)
    assert len(rows) == FK.MAX_ROWS and rows[-1]["test"] == f"tests/t.py::test_{2 * FK.MAX_ROWS}"


def _partial_project(repo: Path, tmp_path: Path, options: str = "") -> None:
    run_cli(repo, "init")
    (repo / "bin").mkdir()
    (repo / "tests").mkdir()
    script = repo / "bin" / "pytest"
    script.write_text(
        "#!/bin/sh\n"
        f"n=$(cat {tmp_path}/calls 2>/dev/null || echo 0); echo $((n+1)) > {tmp_path}/calls\n"
        'if [ "$n" -eq 0 ]; then\n'
        "  printf 'FAILED tests/t.py::test_flaky - x\\nFAILED tests/t.py::test_broken - y\\n"
        "2 failed in 0.1s\\n'; exit 1\n"
        "fi\n"
        "printf 'FAILED tests/t.py::test_broken - y\\n1 failed, 1 passed in 0.1s\\n'; exit 1\n"
    )
    script.chmod(0o755)
    (repo / ".ddflow" / "gates.toml").write_text(
        f'[gate.unit_tests]\ncommand = "./bin/pytest -q {options} tests/"\ncwd = "repo"\n'
    )
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")
    run_cli(repo, "claim", "T1", "--no-worktree", agent="worker")


def test_a_partial_rerun_still_logs_the_tests_that_passed_alone(repo, tmp_path) -> None:
    _partial_project(repo, tmp_path)
    code, out, _e = run_cli(
        repo, "--json", "gate", "run", "T1", "unit_tests", "--rerun-failed", agent="worker"
    )
    assert code == 1
    ev = json.loads(out)["evidence"]
    assert ev["rerun"]["still_failing"] == ["tests/t.py::test_broken"]
    assert ev["flakes_logged"] == 1
    assert [r["test"] for r in FK.read(repo)] == ["tests/t.py::test_flaky"]


def test_a_fail_fast_rerun_logs_no_flake_for_tests_it_never_ran(repo, tmp_path) -> None:
    _partial_project(repo, tmp_path, options="-x")
    code, out, _e = run_cli(
        repo, "--json", "gate", "run", "T1", "unit_tests", "--rerun-failed", agent="worker"
    )
    assert code == 1
    assert json.loads(out)["evidence"]["rerun"]["flaked"] == []
    assert FK.read(repo) == []


def test_a_rerun_then_a_passing_gate_run_is_one_flake_not_two(repo, tmp_path) -> None:
    _project(repo, tmp_path, fails=1)
    assert (
        run_cli(repo, "gate", "run", "T1", "unit_tests", "--rerun-failed", agent="worker")[0] == 1
    )
    assert len(FK.read(repo)) == 1
    assert run_cli(repo, "gate", "run", "T1", "unit_tests", agent="worker")[0] == 0
    assert len(FK.read(repo)) == 1, "the rerun already counted it"


def test_a_bare_header_does_not_name_a_class_method() -> None:
    assert FK._names("test_b", "f.py::test_b")
    assert not FK._names("test_b", "f.py::TestC::test_b")
    assert FK._names("TestC.test_b", "f.py::TestC::test_b")


def test_the_flake_log_masks_a_secret_in_the_test_id_too(repo) -> None:
    run_cli(repo, "init")
    FK.record(repo, [f"tests/t.py::test_h[token={FAKE_KEY}]"])
    (row,) = FK.read(repo)
    assert FAKE_KEY not in row["test"]
