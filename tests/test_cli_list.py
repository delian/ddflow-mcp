"""`ddflow list <kind>`: the CLI twin of `ddflow_list` (bugs B8ea46cd8a9, no-item-listing-command).

It takes the same kinds and filters as `task list`, `bug list` ... and as the MCP tool, and a
task row carries its `needs`, `globs` and `parent`, so a queue can be audited without the
sqlite index.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli


def _seed(repo):
    run_cli(repo, "init")
    run_cli(repo, "phase", "add", "P1", "--title", "phase one")
    run_cli(repo, "task", "add", "T1", "--title", "first", "--phase", "P1", "--globs", "a/**")
    run_cli(repo, "task", "add", "T2", "--title", "second", "--needs", "T1", "--globs", "b.py")


def test_list_is_a_command_and_matches_task_list(repo):
    _seed(repo)
    code, out, err = run_cli(repo, "list", "task", "--json")
    assert code == 0, (out, err)
    code2, out2, _ = run_cli(repo, "task", "list", "--json")
    assert code2 == 0
    assert json.loads(out) == json.loads(out2)


def test_bare_list_lists_tasks_not_a_usage_error(repo):
    """B3581842213: `ddflow list` alone must not die with 'required arguments: kind';

    it lists tasks, exactly what `ddflow list task` shows."""
    _seed(repo)
    code, out, err = run_cli(repo, "list")
    assert code == 0, (out, err)
    code2, out2, _ = run_cli(repo, "list", "task")
    assert code2 == 0 and out == out2


def test_list_rows_carry_needs_and_globs(repo):
    _seed(repo)
    code, out, err = run_cli(repo, "list", "task", "--json")
    assert code == 0, (out, err)
    rows = {r["id"]: r for r in json.loads(out)["rows"]}
    assert rows["T2"]["needs"] == ["T1"]
    assert rows["T2"]["globs"] == ["b.py"]
    assert rows["T1"]["globs"] == ["a/**"]
    assert "parent" in rows["T1"]


def test_list_takes_the_filters_and_kinds(repo):
    _seed(repo)
    code, out, _ = run_cli(repo, "list", "task", "--phase", "P1")
    assert code == 0 and "T1" in out and "T2" not in out
    code, out, _ = run_cli(repo, "list", "phase")
    assert code == 0 and "P1" in out
    code, _, _ = run_cli(repo, "list", "nonsense")
    assert code != 0


def test_list_refuses_a_flag_the_kind_does_not_take(repo):
    _seed(repo)
    code, _, err = run_cli(repo, "list", "task", "--item", "T1")
    assert code == 3 and "--item" in err


def test_list_refusal_under_json_carries_a_body(repo):
    _seed(repo)
    code, out, _ = run_cli(repo, "list", "task", "--item", "T1", "--json")
    body = json.loads(out)
    assert code == 3 and "--item" in body["reason"] and body["schema"] == "list@1"


def _seed_bugs_severities(repo):
    """Filing order monotone against severity: newest-first would reverse it, severity
    order does not, so the two are told apart without knowing the minted ids."""
    run_cli(repo, "init")
    for summary, sev in (("boom crit", "critical"), ("boom mid", "medium"), ("boom none", "")):
        args = ["bug", "found", "--summary", summary, "--no-task"]
        if sev:
            args += ["--severity", sev]
        code, out, err = run_cli(repo, *args)
        assert code == 0, (out, err)


def test_bug_list_sort_severity_orders_severity_first(repo):
    """`bug list --sort severity`: critical first. Without the flag the default order
    stays newest-change-first, which this deliberately differs from."""
    _seed_bugs_severities(repo)
    code, out, err = run_cli(repo, "list", "bug", "--all", "--sort", "severity", "--json")
    assert code == 0, (out, err)
    rows = json.loads(out)["rows"]
    assert [r["severity"] for r in rows] == ["critical", "medium", ""]
    code2, out2, _ = run_cli(repo, "list", "bug", "--all", "--json")
    assert code2 == 0
    assert [r["severity"] for r in json.loads(out2)["rows"]] == ["", "medium", "critical"]


def test_bug_list_sort_severity_edges_in_text(repo):
    """The fix-task edge and its dependency edges are on the `bug list` line, not only
    in the JSON rows."""
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "dep one")
    run_cli(repo, "task", "add", "fix-B1", "--title", "fix carrier", "--needs", "T1")
    code, out, err = run_cli(
        repo,
        "bug",
        "found",
        "--id",
        "B1",
        "--summary",
        "bound broken",
        "--severity",
        "high",
        "--item",
        "fix-B1",
    )
    assert code == 0, (out, err)
    code, out, _ = run_cli(repo, "bug", "list", "--all")
    assert code == 0 and "fix fix-B1" in out and "needs: T1" in out and "high" in out


def test_list_refuses_sort_for_kinds_that_do_not_take_it(repo):
    _seed(repo)
    code, _, err = run_cli(repo, "list", "task", "--sort", "severity")
    assert code == 3 and "--sort" in err
