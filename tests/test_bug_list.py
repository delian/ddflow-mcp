"""Bug list rows: severity, the fix-task edge and the fix task's dependency edges.

The queue engine (`services/viewers.py`) renders a bug row's id/state/title/owner/updated
and the newest-first order; severity ordering and a bug's dependency edges are the
knowledge-level facets `ddflow/api/knowledge/bugs.py` attaches, through `finish_bug_list`
-- the one function the CLI (`viewers_lists.cmd_list`) and the MCP `ddflow_list` shaping
(`surfaces/mcp.py`) both use, so the two surfaces carry the same fields.

Filed for the task "Bug list sorting by severity with dependency edges for CLI and
MCP": the regression tests fail against the unfixed code -- the fields are missing from
every bug row, and no `sort` argument parses.
"""

from __future__ import annotations

import json

import pytest
from conftest import run_cli

from ddflow.api.knowledge import bugs as KB
from ddflow.api.viewers import view_list as api_view_list


def _seed_fix_edges(repo):
    """A fix task with a dependency edge: hand-file `fix-B1` needing `T1`, then bind one
    bug to it (`bug found --item fix-B1`): an open bug-fix task fixing no other bug IS
    the fix, so the bug records `fix_task=fix-B1` and nothing new is filed."""
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "dep one")
    run_cli(repo, "task", "add", "fix-B1", "--title", "fix edge carrier", "--needs", "T1")
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
    run_cli(repo, "bug", "found", "--summary", "loose broken", "--no-task")


def _seed_mix(repo):
    """Bugs in a filing order severity would not ask for: newest-first differs from
    critical-first, so the two orders are distinguishable whatever ids the mints give."""
    run_cli(repo, "init")
    for sev, summary in (
        ("critical", "boom crit"),
        ("high", "boom high one"),
        ("medium", "boom med"),
        ("high", "boom high two"),
        ("", "boom none"),
    ):
        args = ["bug", "found", "--summary", summary, "--no-task"]
        if sev:
            args += ["--severity", sev]
        code, out, err = run_cli(repo, *args)
        assert code == 0, (out, err)


def test_bug_rows_carry_severity_fix_task_and_dependency_edges(repo):
    """A bug row names its severity, the task that fixes it, and that task's dependency
    edges; a bug with no task carries the empty edges honestly."""
    _seed_fix_edges(repo)
    code, out, err = run_cli(repo, "list", "bug", "--all", "--json")
    assert code == 0, (out, err)
    rows = {r["title"]: r for r in json.loads(out)["rows"]}
    assert rows["bound broken"]["severity"] == "high"
    assert rows["bound broken"]["fix_task"] == "fix-B1"
    assert rows["bound broken"]["needs"] == ["T1"]
    assert rows["loose broken"]["fix_task"] == ""
    assert rows["loose broken"]["needs"] == []


def test_finish_bug_list_orders_by_severity_then_id(repo):
    """`sort="severity"` orders the whole matched set critical..low with a stable id
    tiebreak (a severity unknown or unset ranks last); rows carry the edge fields. The
    expected order is asserted literally -- the declared critical..high..medium..low
    wording, not via the `severity_rank` helper under test."""
    _seed_mix(repo)
    out = api_view_list(repo, "bug", limit=0, offset=0)
    assert out.ok
    out = KB.finish_bug_list(repo, out, sort="severity")
    sevs = [r["severity"] for r in out.data["rows"]]
    assert sevs == ["critical", "high", "high", "medium", ""]
    keys = [(KB.severity_rank(r["severity"]), r["id"]) for r in out.data["rows"]]
    assert keys == sorted(keys)
    for r in out.data["rows"]:
        assert {"severity", "fix_task", "needs"} <= set(r)


def test_finish_bug_list_refuses_other_sort_words(repo):
    _seed_mix(repo)
    out = api_view_list(repo, "bug", limit=50, offset=0)
    with pytest.raises(ValueError):
        out = KB.finish_bug_list(repo, out, sort="urgent")


def test_finish_bug_list_refuses_sort_for_kinds_that_do_not_take_it(repo):
    """The MCP parity of the CLI's `--sort` is a bug-only selector: a `sort` asked for
    any other kind is refused here, since the one shared `ddflow_list` schema cannot
    say it per kind (the CLI refuses it in the parser)."""
    _seed_mix(repo)
    out = api_view_list(repo, "task", limit=50, offset=0)
    with pytest.raises(ValueError, match="kind=bug only"):
        KB.finish_bug_list(repo, out, sort="severity")


def test_mcp_list_serves_the_non_bug_sort_refusal(repo):
    """Over MCP the same request is an outcome REFUSED with a reason and a list-shaped
    body (exit 3, the CLI's refusal), not a Value/TypeError the dispatcher would
    answer as a malformed call."""
    from ddflow.api.viewers import view_list as _vl
    from ddflow.surfaces.mcp import _finish_list_read

    _seed_mix(repo)
    refused = _finish_list_read(
        repo, _vl(repo, "task", limit=50, offset=0), {"kind": "task", "sort": "severity"}
    )
    assert refused.exit == 3 and "kind=bug only" in refused.reason


def test_mcp_list_tool_declares_the_sort_argument(repo):
    """Over MCP the knob is the `sort` property `ddflow_list` accepts: the same
    `_properties` call serves the schema and the argument check, and the shaping in
    `_call_api` finishes bug rows with it."""
    from ddflow.surfaces.mcp import TOOLS, _properties

    assert "sort" in _properties(TOOLS["ddflow_list"])


def test_mcp_serves_bug_sort(repo):
    """An MCP `ddflow_list` for kind=bug with `sort="severity"` goes through
    `finish_bug_list` like the CLI read does: the served rows come back in severity
    order and carry the same facets (severity, fix_task, needs) the CLI advertises."""
    from ddflow.api.viewers import view_list as _vl
    from ddflow.surfaces.mcp import _finish_list_read

    _seed_mix(repo)
    done = _finish_list_read(
        repo, _vl(repo, "bug", limit=50, offset=0), {"kind": "bug", "sort": "severity"}
    )
    assert done.exit == 0
    assert [r["severity"] for r in done.data["rows"]] == [
        "critical",
        "high",
        "high",
        "medium",
        "",
    ]
    for r in done.data["rows"]:
        assert {"severity", "fix_task", "needs"} <= set(r)


def test_bug_sort_orders_the_page_it_repaginates(repo):
    """Severity order is stable to paging: --limit cuts AFTER the severity order, so a
    page of 1 shows the critical bug, not the newest. The expectation names the bug
    literally: the critical bug rows first, whatever rank bookkeeping the helper uses."""
    _seed_mix(repo)
    code, out, err = run_cli(
        repo, "list", "bug", "--all", "--limit", "1", "--sort", "severity", "--json"
    )
    assert code == 0, (out, err)
    body = json.loads(out)
    assert len(body["rows"]) == 1
    assert body["rows"][0]["severity"] == "critical"
    assert body["rows"][0]["title"] == "boom crit"
    # The re-cut page keeps the caller's page size as its advertised limit, not the
    # whole-matched-set refetch's (limit=0 → the cap).
    assert body["limit"] == 1


def test_bug_sort_limit_zero_pages_the_whole_set(repo):
    """The "all rows" limit is the engine's whole set (the engine normalizes 0 to its
    cap before the rows come back), not an empty page: `--limit 0 --sort severity`
    returns every matched bug in severity order, last page, no `next`."""
    _seed_mix(repo)
    code, out, err = run_cli(
        repo, "list", "bug", "--all", "--limit", "0", "--sort", "severity", "--json"
    )
    assert code == 0, (out, err)
    body = json.loads(out)
    assert [r["severity"] for r in body["rows"]] == ["critical", "high", "high", "medium", ""]
    assert body["next"] is None


def test_mcp_bug_sort_serves_the_owner_filter(repo):
    """Kind=bug has no `owner` filter on either surface: the engine refuses `agent` on
    bugs (a bug row's owner is "", and the kinds that DO echo `owner` are the
    task/phase/lesson/session reads in tests/test_view_mcp.py). So a severity-sorted
    refetch replays the filters a bug read DOES echo -- `state` (the open default) here
    -- and an `owner` asked on a bug read is the engine's own refusal (exit 3), not an
    echoed filter key."""
    from ddflow.api.viewers import view_read as _vr
    from ddflow.surfaces.mcp import _finish_list_read

    _seed_mix(repo)
    asked = _vr(repo, "bug", owner="alice", limit=50, offset=0)
    assert asked.exit == 3
    assert "no agent to filter by" in asked.reason
    assert "owner" not in asked.data["filters"]
    result = _vr(repo, "bug", limit=50, offset=0)
    assert result.data["filters"] == {"state": "open"}
    done = _finish_list_read(repo, result, {"kind": "bug", "sort": "severity"})
    assert done.exit == 0
    assert [r["severity"] for r in done.data["rows"]] == [
        "critical",
        "high",
        "high",
        "medium",
        "",
    ]


def test_bug_sort_pages_rows_past_the_engine_cap(repo, monkeypatch):
    """The whole-set refetch the engine caps (`limit=0` -> `MAX_LIMIT`) must page the
    matched set, or a page asked AT the cap is an empty, non-advancing one: its rows
    never move `next` past the cap and everything beyond it stays unreachable. With the
    cap pinned at 3 and 5 matched bugs, the page at offset 3 carries the two rows the
    cap hid, in severity order, and ends the walk (`next=None`), while the first page
    keeps the caller's page size."""
    from ddflow.services import viewers as V

    monkeypatch.setattr(V, "MAX_LIMIT", 3)
    _seed_mix(repo)
    first = KB.finish_bug_list(repo, api_view_list(repo, "bug", limit=3, offset=0), sort="severity")
    assert first.data["total"] == 5
    assert len(first.data["rows"]) == 3  # the caller's page size, cut after the order
    tail = KB.finish_bug_list(repo, api_view_list(repo, "bug", limit=3, offset=3), sort="severity")
    assert [r["severity"] for r in tail.data["rows"]] == ["medium", ""]
    assert first.data["next"] == 3
    assert tail.data["next"] is None
