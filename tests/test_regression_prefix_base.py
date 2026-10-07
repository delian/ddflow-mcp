"""A regression test checked AFTER the fix merged is run against the landing's own
before/after, not the moving base ref (B0d5253d31f).

`bug fixed` (and `complete fix-<BUG> --regression-test`) built the pre-fix tree from
the fix task's base by NAME. The driver merges first and completes after, so by then
`main` holds the fix: with the worktree removed by the merge the check was always
`could-not-run`, and with it kept a genuine fail-first test was refused as passing on the
"pre-fix" tree.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import pass_pipeline, run_cli
from test_bug_regression_verified import _case

from ddflow import api

OK = 0


def test_a_fail_first_test_is_verified_after_the_fix_merged(repo):
    _case(repo)
    pass_pipeline(repo, "fix-B1")
    code, out, err = run_cli(repo, "merge", "fix-B1")
    assert code == OK, out + err
    res = api.bug_fixed(repo, "B1", regression_test="tests/test_f.py::test_f")
    assert res.exit == OK, res.reason
    assert res.data["regression_verified"] == "verified", res.data
    ev = res.data["regression_verify"]
    assert ev["prefix_outcome"] == "failed" and ev["fix_outcome"] == "passed", ev
    assert "/" not in ev["tree"], "the scratch checkout's path, which no longer exists"


def test_a_test_that_passes_before_the_landing_is_still_refused(repo):
    wt = _case(repo)
    (wt / "tests" / "test_always.py").write_text("def test_always():\n    assert True\n")
    import subprocess

    subprocess.run(["git", "-C", str(wt), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(wt), "commit", "-qm", "always"], check=True)
    pass_pipeline(repo, "fix-B1")
    code, out, err = run_cli(repo, "merge", "fix-B1")
    assert code == OK, out + err
    res = api.bug_fixed(repo, "B1", regression_test="tests/test_always.py::test_always")
    assert res.exit == 3, res
    assert "pre-fix tree" in res.reason
