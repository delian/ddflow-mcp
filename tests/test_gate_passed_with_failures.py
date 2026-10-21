"""A command gate that exits 0 with failing tests in its output is not a plain pass
(B-ci-gate-prints-passed-with-failing-tests).

A gate listed 5 failing tests and was recorded `passed`. Now each failing test is run alone
by ddflow: `passed` only when every one of them passed on that rerun, and the verdict says
which were rerun. A failure with no recorded passing rerun is never `passed`. The log
prints ONE verdict line (the command's own `ci: passed` and the gate's `ci: PASSED` used to
be printed one after the other).
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

ID = "tests/test_x.py::test_a"


def _project(repo: Path, *, rerun_ok: bool, command: str = "./bin/pytest -q tests/") -> None:
    """A fake pytest that lists ``test_a`` as FAILED yet exits 0 (a wrapper that masks the
    exit) when run on the suite, and passes or fails it when run on the node id alone."""
    run_cli(repo, "init")
    (repo / "bin").mkdir()
    (repo / "tests").mkdir()
    (repo / "tests" / "test_x.py").write_text("def test_a():\n    pass\n")
    script = repo / "bin" / "pytest"
    script.write_text(
        "#!/bin/sh\n"
        'case "$*" in *::*)\n'
        + (
            '  echo "1 passed in 0.1s"; exit 0;;\n'
            if rerun_ok
            else f'  echo "FAILED {ID} - boom"; echo "1 failed in 0.1s"; exit 1;;\n'
        )
        + "esac\n"
        f'printf "FAILED {ID} - boom\\n1 failed, 3 passed in 0.1s\\nunit_tests: passed\\n"; exit 0\n'
    )
    script.chmod(0o755)
    (repo / ".ddflow" / "gates.toml").write_text(
        f'[gate.unit_tests]\ncommand = {json.dumps(command)}\ncwd = "repo"\n'
    )
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "project"], check=True)
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")
    run_cli(repo, "claim", "T1", "--no-worktree", agent="worker")


def _record(repo: Path):
    return fold(EventLog(repo).read_all(), strict=False).items["T1"].gates["unit_tests"]


def test_failures_that_pass_alone_pass_and_the_verdict_names_them(repo) -> None:
    _project(repo, rerun_ok=True)
    code, out, err = run_cli(repo, "gate", "run", "T1", "unit_tests", agent="worker")
    assert code == 0, out + err
    rec = _record(repo)
    assert rec.outcome == "passed"
    assert f"passed after rerunning 1 test: {ID}, each passed on rerun" in rec.evidence["reason"]
    assert rec.evidence["rerun"]["flaked"] == [ID]
    assert f"passed after rerunning 1 test: {ID}" in out


def test_a_failure_that_fails_alone_never_yields_passed(repo) -> None:
    _project(repo, rerun_ok=False)
    code, out, err = run_cli(repo, "gate", "run", "T1", "unit_tests", agent="worker")
    assert code == 1, out + err
    rec = _record(repo)
    assert rec.outcome == "failed"
    assert ID in rec.reason


def test_a_failure_that_cannot_be_rerun_never_yields_passed(repo) -> None:
    cmd = f"{sys.executable} -c \"print('FAILED {ID} - boom')\""
    _project(repo, rerun_ok=True, command=cmd)
    code, out, err = run_cli(repo, "gate", "run", "T1", "unit_tests", agent="worker")
    assert code != 0, out + err
    rec = _record(repo)
    assert rec.outcome in ("failed", "unavailable")
    assert "no recorded passing rerun" in rec.evidence["reason"] or ID in rec.reason


def test_the_log_prints_one_verdict(repo) -> None:
    _project(repo, rerun_ok=True)
    _c, out, _e = run_cli(repo, "gate", "run", "T1", "unit_tests", agent="worker")
    verdicts = [ln for ln in out.splitlines() if ln.lower().startswith("unit_tests: passed")]
    assert len(verdicts) <= 1, out
    assert not any(ln.strip().lower() == "unit_tests: passed" for ln in out.splitlines()), out
