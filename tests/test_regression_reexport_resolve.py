"""`bug fixed` resolves a re-exported test through pytest (bug Ba6c8cdeda4).

The static name check read only `def`/`class` in the named file, so a test a module imports
was reported as existing in no worktree and the close was refused.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from test_bug_regression_verified import _commit, _fixed_events, _worktree
from test_regression_testfix import BROKEN

from ddflow import api

OK, REFUSED = 0, 3


def _case(repo: Path) -> Path:
    run_cli(repo, "init")
    run_cli(repo, "config", "--set", "gate.unit_tests.command", f"{sys.executable} -m pytest -q")
    (repo / "tests").mkdir()
    (repo / "tests" / "helper.py").write_text(BROKEN)
    (repo / "tests" / "test_v.py").write_text("from helper import test_v  # noqa: F401\n")
    _commit(repo, "the test as it was")
    run_cli(repo, "bug", "found", "--id", "B1", "--summary", "the shared fixture is wrong")
    code, _out, err = run_cli(repo, "claim", "fix-B1")
    assert code == OK, err
    wt = _worktree(repo, "fix-B1")
    (wt / "tests" / "test_v.py").write_text(
        "import helper\n\nhelper.VALUE = 2\nfrom helper import test_v  # noqa: F401\n"
    )
    _commit(wt, "fix the fixture where the test is collected")
    return wt


def test_a_re_exported_test_is_resolved_and_the_bug_closes(repo):
    _case(repo)
    out = api.bug_fixed(repo, "B1", regression_test="tests/test_v.py::test_v")
    assert out.exit == OK, out.reason
    assert out.data["regression_verified"] == "verified", out.data
    (data,) = _fixed_events(repo)
    assert data["regression_verify"]["verified_by"] == "pre-fix-test-files"


def test_a_name_nothing_provides_is_still_refused(repo):
    _case(repo)
    out = api.bug_fixed(repo, "B1", regression_test="tests/test_v.py::test_nope")
    assert out.exit != OK and "exist in no worktree" in out.reason, out
