"""`ddflow_list`: ONE read tool over the viewers (task|phase|bug|research|session|search).

Each kind returns the rows the CLI's `--json` prints; a read is bounded (default 25 rows,
a session shown in full cut to its newest entries) and says so; an unknown kind or a bad
filter is a refusal; and tools/list pays for one tool, not six.
"""

from __future__ import annotations

import json

import pytest
from conftest import run_cli
from helpers import tool_json as _body

from ddflow.infra.log import EventLog
from ddflow.surfaces import mcp_bound as B
from ddflow.surfaces.mcp import TOOLS, Server


def _call(repo, **arguments):
    r = Server(repo, agent="agent-test").handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "ddflow_list", "arguments": arguments},
        }
    )["result"]
    return r


@pytest.fixture
def proj(repo):
    assert run_cli(repo, "init")[0] == 0
    run_cli(repo, "phase", "add", "P1", "--title", "Auth")
    run_cli(repo, "task", "add", "P1.T1", "--phase", "P1", "--title", "login", "--tags", "web")
    run_cli(repo, "task", "add", "P1.T2", "--phase", "P1", "--title", "logout")
    # --no-task keeps the phase progress below about the tasks filed by hand.
    bug = ("bug", "found", "--no-task")
    run_cli(repo, *bug, "--id", "B1", "--summary", "crash on login", "--item", "P1.T1")
    run_cli(repo, *bug, "--id", "B2", "--summary", "bad total", "--item", "P1.T2")
    run_cli(repo, "bug", "invalid", "B2", "--reason", "not a bug")
    argv = ("research", "--id", "R1", "--question", "does x work", "--verdict", "CONFIRMED")
    assert run_cli(repo, *argv, "--probe", "ran it")[0] == 0
    log = EventLog(repo, "alice")
    log.append("session.started", "S1", {"model": "m1"})
    log.append("session.prompt", "S1", {"text": "first ask", "item": "P1.T1"})
    return repo


def _ids(body):
    return [r["id"] for r in body["rows"]]


@pytest.mark.parametrize("kind", ["task", "phase", "bug", "research", "session"])
def test_each_list_kind_returns_the_cli_rows(proj, kind):
    argv = ("session", "list") if kind == "session" else (kind, "list")
    code, out, _ = run_cli(proj, *argv, "--json")
    assert code == 0, out
    r = _call(proj, kind=kind)
    assert not r.get("isError"), r
    body = _body(r)
    assert body["record_kind"] == kind
    assert _ids(body) == _ids(json.loads(out))
    assert body["rows"] and body["total"] == len(body["rows"])


def test_bug_defaults_to_open_and_all_includes_the_rest(proj):
    assert _ids(_body(_call(proj, kind="bug"))) == ["B1"]
    assert sorted(_ids(_body(_call(proj, kind="bug", all=True)))) == ["B1", "B2"]


def test_phase_rows_carry_progress(proj):
    row = _body(_call(proj, kind="phase"))["rows"][0]
    assert (row["done"], row["total"]) == (0, 2)


def test_filters_reach_the_engine(proj):
    assert _ids(_body(_call(proj, kind="task", tag="web"))) == ["P1.T1"]
    assert _ids(_body(_call(proj, kind="session", owner="alice"))) == ["S1"]


def test_search_finds_a_record(proj):
    body = _body(_call(proj, kind="search", query="logout"))
    assert body["record_kind"] == "search" and any(r["id"] == "P1.T2" for r in body["rows"])
    assert _call(proj, kind="search", query="zzz-no-such")["_meta"]["exit"] == 2


def test_session_id_shows_one_session_in_full(proj):
    body = _body(_call(proj, kind="session", id="S1"))
    assert body["id"] == "S1" and body["entries"][0]["text"] == "first ask"


def test_refusals(proj):
    stray = (
        {"kind": "session", "tag": "x"},
        {"kind": "session", "phase": "P1"},
        {"kind": "search", "query": "x", "id": "S1"},
        {"kind": "search", "query": "x", "tag": "web"},
        {"kind": "task", "query": "login"},
        {"kind": "task", "mode": "regex"},
        {"kind": "task", "all": True},
        {"kind": "bug", "sources": "task"},
    )
    for args in ({"kind": "nope"}, {"kind": "search"}, {"kind": "task", "id": "S1"}, *stray):
        r = _call(proj, **args)
        assert r["_meta"]["exit"] == 3, (args, r)
    assert _call(proj, kind="session", id="nope")["_meta"]["exit"] == 3


def test_a_list_is_bounded_and_says_so(repo):
    run_cli(repo, "init")
    log = EventLog(repo, "agent-test")
    for i in range(40):
        log.append("task.added", f"T{i:02d}", {"title": f"task {i}"})
    body = _body(_call(repo, kind="task"))
    assert len(body["rows"]) == B.ROWS_SHOWN == body["shown"]
    assert body["total"] == 40 and body["truncated"] is True
    assert len(_body(_call(repo, kind="task", limit=0))["rows"]) == 40
    assert len(_body(_call(repo, kind="task", limit=3))["rows"]) == 3


def test_a_long_session_is_cut_to_its_newest_entries(repo):
    run_cli(repo, "init")
    log = EventLog(repo, "alice")
    log.append("session.started", "S1", {"model": "m"})
    for i in range(60):
        log.append("session.note", "S1", {"text": f"note {i} " + "x" * 400})
    r = _call(repo, kind="session", id="S1")
    body = _body(r)
    assert len(body["entries"]) == B.ROWS_SHOWN
    assert body["entries"][-1]["text"].startswith("note 59")
    assert all(len(e["text"]) < 200 for e in body["entries"])
    assert any("truncated" in c["text"] for c in r["content"][1:])
    full = _body(_call(repo, kind="session", id="S1", limit=0))
    assert len(full["entries"]) == 60


def test_one_tool_not_six():
    assert "ddflow_list" in TOOLS
    assert not [t for t in TOOLS if t.startswith(("ddflow_task_list", "ddflow_session_list"))]
    assert len(TOOLS["ddflow_list"]["description"]) < 700


def test_the_owner_filter_is_echoed_under_its_own_name_where_a_kind_has_one(proj):
    # bug and research have no owner to filter by (the engine refuses it).
    for kind in ("task", "phase", "session"):
        assert _body(_call(proj, kind=kind, owner="alice", limit=5))["filters"] == {
            "owner": "alice"
        }, kind
