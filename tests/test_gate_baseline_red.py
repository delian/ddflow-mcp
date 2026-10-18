"""A ci / unit_tests failure that the base branch shows too is baseline-red (DX-gate-baseline-red).

The failing ids are run again, alone, on the base in a scratch worktree. All of them red there:
the gate is recorded `partial` with `baseline-red: <ids>, bug <id>`, a bug is filed, and
`complete` accepts the coverage gap. One of them green there (the branch's own fault): the
gate stays FAILED.
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


def _project(repo: Path, *, base_healthy: bool = False) -> None:
    """A fake pytest that fails ``test_a`` unless the file `healthy` exists. With
    ``base_healthy`` the committed base has it."""
    run_cli(repo, "init")
    if base_healthy:
        (repo / "healthy").write_text("")
    (repo / "bin").mkdir()
    (repo / "tests").mkdir()
    (repo / "tests" / "test_x.py").write_text("def test_a():\n    pass\n")
    script = repo / "bin" / "pytest"
    script.write_text(
        "#!/bin/sh\n"
        'if [ -e "$(dirname "$0")/../healthy" ]; then echo "1 passed in 0.1s"; exit 0; fi\n'
        "printf '____ test_a ____\\nE assert 1 == 2\\n=== short test summary info ===\\n"
        f"FAILED {ID} - assert 1 == 2\\n1 failed in 0.1s\\n'; exit 1\n"
    )
    script.chmod(0o755)
    (repo / ".ddflow" / "gates.toml").write_text(
        '[gate.unit_tests]\ncommand = "./bin/pytest -q tests/"\ncwd = "repo"\n'
    )
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "project"], check=True)
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")
    run_cli(repo, "claim", "T1", "--no-worktree", agent="worker")


def test_a_failure_the_base_shows_too_is_recorded_partial_baseline_red(repo) -> None:
    _project(repo)  # the base fails test_a: so does the item's tree
    code, out, _e = run_cli(repo, "--json", "gate", "run", "T1", "unit_tests", agent="worker")
    body = json.loads(out)
    assert body["outcome"] == "partial", body
    assert code != 1
    rec = fold(EventLog(repo).read_all(), strict=False).items["T1"].gates["unit_tests"]
    assert rec.outcome == "partial"
    assert rec.reason.startswith(f"baseline-red: {ID}, bug B"), rec.reason
    assert rec.evidence["baseline"]["red"] and rec.evidence["baseline"]["ids"] == [ID]
    bug = rec.reason.rsplit("bug ", 1)[1]
    assert bug in fold(EventLog(repo).read_all(), strict=False).bugs


def test_a_failure_the_base_does_not_show_stays_failed(repo) -> None:
    _project(repo, base_healthy=True)
    (repo / "healthy").unlink()  # the item's tree breaks what the base has
    code, out, _e = run_cli(repo, "--json", "gate", "run", "T1", "unit_tests", agent="worker")
    body = json.loads(out)
    assert code == 1 and body["outcome"] == "failed", body
    assert body["evidence"]["baseline"]["red"] is False


def test_unattributable_failures_stay_failed(repo) -> None:
    from ddflow.services.gates import baseline_red as BR

    ev = {"failed_tests": [ID], "failed_tests_more": 3}
    assert BR.candidate_ids("unit_tests", "pytest", ev, False) == []
    assert BR.candidate_ids("unit_tests", "pytest -x", {"failed_tests": [ID]}, True) == []
    assert BR.candidate_ids("critic", "pytest", {"failed_tests": [ID]}, False) == []
    assert BR.candidate_ids("ci", "pytest", {"failed_tests": [ID]}, False) == [ID]
