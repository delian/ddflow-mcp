"""Bd10386f03b: a test-only fix that already LANDED is still a test-only fix.

The landing commit also carries ddflow's own state (`.ddflow/events/*.jsonl`, blobs), so
"every changed file is a test file" was false and the close was refused as 'passes on the
pre-fix tree' though only the test changed. State files are not part of the fix."""

import subprocess
import sys

sys.path.insert(0, "tests")
from conftest import run_cli
from test_bug_regression_verified import _commit
from test_regression_testfix import FLAKY_BASE, GUARDED

from ddflow import api


def test_a_landed_test_only_fix_is_verified_by_mutation_despite_ddflow_state_in_the_landing(repo):
    run_cli(repo, "init")
    run_cli(repo, "config", "--set", "gate.unit_tests.command", f"{sys.executable} -m pytest -q")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_h.py").write_text(FLAKY_BASE)
    _commit(repo, "the test as it was")
    run_cli(repo, "bug", "found", "--id", "B1", "--summary", "flaky test")
    (repo / "tests" / "test_h.py").write_text(GUARDED)
    _commit(repo, "guard")
    sha = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    out = api.bug_fixed(
        repo,
        "B1",
        regression_test="tests/test_h.py::test_h",
        landed=sha,
        mutation={"file": "tests/test_h.py", "old": "GUARD = True", "new": "GUARD = False"},
    )
    assert out.exit == 0, (out.reason, out.data)
    assert out.data["regression_verified"] == "verified-by-mutation"
