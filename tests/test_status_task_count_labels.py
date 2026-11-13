"""`ddflow status` says what its ``D/T tasks complete`` counts, so the headline and the
completion ``Progress:`` block are visibly two definitions, not one pair disagreeing
(B4b1150d89a).

On the filing of the bug, `ddflow status` printed "1334/3056 tasks complete" while the
`Progress:` block a completion printed "tasks 913/2462" -- different totals, neither
saying why. The status headline counts every task record (abandoned tasks and fix tasks
included); the progress block counts live work items only (fix tasks and abandoned ones
out). The note under the headline must name both definitions.
"""

from __future__ import annotations

import re

from conftest import run_cli


def _project(repo, abandon: bool = False):
    run_cli(repo, "init")
    run_cli(repo, "phase", "add", "P1", "--title", "p")
    for t in ("T1", "T2", "T3"):
        assert (
            run_cli(repo, "task", "add", t, "--title", t, "--phase", "P1", "--globs", f"{t}.py")[0]
            == 0
        )
    assert (
        run_cli(
            repo,
            "bug",
            "found",
            "--summary",
            "b one breaks",
            "--severity",
            "high",
            "--globs",
            "x.py",
            "--new",
        )[0]
        == 0
    )
    assert run_cli(repo, "complete", "T1", "--force")[0] == 0
    if abandon:
        assert run_cli(repo, "abandon", "T3", "--reason", "not needed")[0] == 0


def _project_without_bug(repo):
    run_cli(repo, "init")
    run_cli(repo, "phase", "add", "P1", "--title", "p")
    for t in ("T1", "T2", "T3"):
        assert (
            run_cli(repo, "task", "add", t, "--title", t, "--phase", "P1", "--globs", f"{t}.py")[0]
            == 0
        )
    assert run_cli(repo, "complete", "T1", "--force")[0] == 0


def test_status_headline_says_what_it_counts(repo):
    _project(repo, abandon=True)
    code, out, err = run_cli(repo, "status")
    assert code == 0, err
    # The headline pair must carry its definition: the abandoned T3 and the auto-filed
    # fix task stay in this count, and the contracts they name either definition with.
    assert "1 of 2 live tasks done (1 abandoned and 1 fix tasks listed separately)" in out
    assert "= 4" in out


def test_progress_block_and_status_headline_count_different_sets_reconcilably(repo):
    _project(repo, abandon=True)
    code, status_out, _ = run_cli(repo, "status")
    assert code == 0
    m = re.search(r"(\d+) of (\d+) live tasks done .* across (\d+) phase", status_out)
    assert m, status_out
    # Live work tasks: T1 done and T2; the abandoned T3 and the auto-filed fix task are
    # listed apart, and all four records are in the printed buckets.
    assert (int(m[1]), int(m[2])) == (1, 2)
    assert "= 4" in status_out
    code, complete_out, err = run_cli(repo, "complete", "T2", "--force")
    assert code == 0, err
    # Live work items only: the abandoned T3 and the fix task are out of this pair.
    assert "tasks 2/2 (100%)" in complete_out
    assert "live tasks" in complete_out  # the block says it is the live pair
    assert "2 of 2 live tasks done" in run_cli(repo, "status")[1]  # the same pair


def test_with_no_abandoned_or_fix_records_headline_and_progress_agree(repo):
    """With no abandoned or fix records at all the two pairs agree, and say it."""
    _project_without_bug(repo)
    code, out, _ = run_cli(repo, "status")
    assert code == 0
    assert "1 of 3 live tasks done (0 abandoned and 0 fix tasks listed separately)" in out
    code, complete_out, err = run_cli(repo, "complete", "T2", "--force")
    assert code == 0, err
    assert "tasks 2/3 (66%)" in complete_out
    # With T1 and T2 done the two surfaces agree (2/3).
    code, status_after, _ = run_cli(repo, "status")
    assert "2 of 3 live tasks done" in status_after
