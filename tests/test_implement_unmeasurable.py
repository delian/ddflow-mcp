"""`gate record implement --outcome passed` is refused when the branch cannot be measured (B2e324cdb87).

`_implement_without_commits` promised "a tree whose range cannot be measured is refused, not
passed" and let the pass through whenever `rev-list base..HEAD` failed (an unknown base ref).
"""

from __future__ import annotations

from conftest import run_cli
from test_gate_record_implement_ahead import _claimed, _implement, _outcome


def test_an_unmeasurable_range_is_refused_not_passed(repo):
    _claimed(repo)
    assert run_cli(repo, "config", "worktree.base_ref", "no-such-ref", agent="a1")[0] == 0
    code, out, err = _implement(repo)
    assert code == 3 and "could not be measured" in out + err, (code, out, err)
    assert _outcome(repo) == ""


def test_force_still_records_over_an_unmeasurable_range(repo):
    _claimed(repo)
    assert run_cli(repo, "config", "worktree.base_ref", "no-such-ref", agent="a1")[0] == 0
    code, out, err = _implement(repo, "--force", "--reason", "base ref is being renamed")
    assert code == 0, out + err
    assert _outcome(repo) == "passed"
