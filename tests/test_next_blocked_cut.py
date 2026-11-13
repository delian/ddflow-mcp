"""The `next` text answers say when the blocked list is cut, never silently.

`next_text` showed up to 10 blocked rows (6 beside a ready list) with no marker when more
existed, so `ddflow next --phase P-swarm` printed "17 blocked" then 10 rows while `--json`
held all 17 (B-next-text-cuts-blocked-silently). A cut list names the count and how to
reach the rest, exactly as `plan`'s lint list already does.
"""

from conftest import run_cli


def _blocked_items(repo, n) -> None:
    run_cli(repo, "init")
    for i in range(n):
        run_cli(repo, "task", "add", f"B{i}", "--title", f"blocked {i}")
        run_cli(repo, "block", f"B{i}", "--reason", "parked for the test")


def test_blocked_list_cut_says_how_many_more_when_nothing_is_ready(repo):
    _blocked_items(repo, 12)
    code, out, _err = run_cli(repo, "next")
    assert code == 2, (code, out)
    assert "12 blocked" in out, out
    # 10 of 12 shown, and the cut says the rest exist and how to reach them.
    assert "... and 2 more" in out, out
    assert "--json" in out, out


def test_blocked_list_cut_is_counted_beside_a_ready_list_too(repo):
    _blocked_items(repo, 8)
    run_cli(repo, "task", "add", "R0", "--title", "ready", "--globs", "r0.py")
    code, out, _err = run_cli(repo, "next")
    assert code == 0, (code, out)
    assert "8 blocked" in out, out
    # 6 of 8 shown beside the ready list, and the cut says how many more.
    assert "... and 2 more" in out, out
