"""`ddflow show <bug id>` shows the bug (bug B-show-bug-id).

It answered "no such item" for a recorded bug, so a bug's summary, its fix and its
regression test could be read only by grepping `.ddflow/events` -- while the brief's own
footer says "`ddflow show <id>` for detail" and agents are handed bug ids to show.
"""

from __future__ import annotations

import json

from conftest import run_cli

from ddflow.infra.log import EventLog


def _bug(repo, summary: str, *extra: str) -> str:
    code, out, err = run_cli(repo, "--json", "bug", "found", "--summary", summary, *extra)
    assert code == 0, err
    return json.loads(out)["id"]


def test_show_resolves_an_open_bug_and_the_items_that_fix_it(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")
    # --no-task: this test is about the hand-written fix tasks `show` recognises, not the
    # one `bug found` files (tests/test_bugs_as_items.py).
    bid = _bug(repo, "the widget drops its last row", "--item", "T1", "--no-task")
    run_cli(repo, "task", "add", "FIX", "--title", f"Keep the last row (fixes bug {bid})")
    run_cli(repo, "task", "add", "OTHER", "--title", "unrelated", "--body", f"see {bid}x")
    run_cli(repo, "task", "add", "TALK", "--title", f"Investigate {bid}")
    run_cli(repo, "task", "add", "NEAR", "--title", f"Fixed rows and see {bid}")
    run_cli(repo, "task", "add", "LIST", "--title", f"Two at once (fixes bugs B0, {bid})")
    run_cli(repo, "task", "add", "BODY", "--title", "Repair", "--body", f"Fixing {bid} here.")

    code, out, err = run_cli(repo, "show", bid)
    assert code == 0, err
    assert f"{bid} [bug] open" in out, out
    assert "the widget drops its last row" in out and " on T1" in out, out
    assert "fix task(s): BODY, FIX, LIST\n" in out and "OTHER" not in out, out
    assert "mentioned by: NEAR, TALK" in out, "a task that only discusses a bug is not its fix"

    code, out, err = run_cli(repo, "--json", "show", bid)
    assert code == 0, err
    body = json.loads(out)
    assert (body["id"], body["kind"], body["state"]) == (bid, "bug", "open")
    assert body["summary"] == "the widget drops its last row" and body["fixing"] == [
        "BODY",
        "FIX",
        "LIST",
    ]


def test_show_names_a_closed_bugs_regression_tests_and_an_invalid_ones_reason(repo):
    run_cli(repo, "init")
    fixed = _bug(repo, "first")
    EventLog(repo, "agent-test").append(
        "bug.fixed",
        fixed,
        {
            "regression_test": "tests/t.py::a, tests/t.py::b",
            "regression_tests": ["tests/t.py::a", "tests/t.py::b"],
        },
    )
    code, out, _err = run_cli(repo, "show", fixed)
    assert code == 0 and f"{fixed} [bug] fixed" in out, out
    assert "tests/t.py::a" in out and "tests/t.py::b" in out, out

    false = _bug(repo, "second")
    assert run_cli(repo, "bug", "invalid", false, "--reason", "works as designed")[0] == 0
    code, out, _err = run_cli(repo, "show", false)
    assert code == 0 and f"{false} [bug] invalid" in out and "works as designed" in out, out


def test_mcp_show_returns_the_bug(repo):
    from ddflow.surfaces.mcp import Server

    run_cli(repo, "init")
    bid = _bug(repo, "over the wire")
    r = Server(repo, agent="agent-test").handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "ddflow_show", "arguments": {"id": bid}},
        }
    )["result"]
    assert not r.get("isError"), r
    body = json.loads(r["content"][0]["text"])
    assert body["kind"] == "bug" and body["summary"] == "over the wire", body


def test_an_id_that_is_neither_says_so(repo):
    run_cli(repo, "init")
    code, _out, err = run_cli(repo, "show", "NOPE")
    assert code == 1 and "no such item or bug 'NOPE'" in err, err


def test_a_fix_after_an_invalid_closure_reads_as_fixed(repo):
    """roborev job 954: a bug closed invalid and later fixed has both closures on record;
    `Bug.resolution` says fixed, and show must not present it as both."""
    run_cli(repo, "init")
    bid = _bug(repo, "third")
    assert run_cli(repo, "bug", "invalid", bid, "--reason", "works as designed")[0] == 0
    EventLog(repo, "agent-test").append("bug.fixed", bid, {"regression_test": ""})
    code, out, _err = run_cli(repo, "show", bid)
    assert code == 0 and f"{bid} [bug] fixed" in out, out
    assert "earlier closed" in out and "superseded by the fix" in out, out
    assert "regression test(s):" not in out, "no test was recorded, so none is announced"


def test_the_last_id_of_an_and_joined_list_is_a_fix(repo):
    """Bug Bfc863d295f: "fixes bugs A, B and X" -- the convention `_show_bug` names --
    listed the fix of X as a mere mention: the list pattern needed a comma after every
    element, so the element before "and" could never be passed over."""
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")
    bid = _bug(repo, "the widget drops its last row", "--item", "T1", "--no-task")
    run_cli(repo, "task", "add", "AND3", "--title", f"Three (fixes bugs B0, B1 and {bid})")
    run_cli(repo, "task", "add", "AND2", "--title", f"Two (fixes bugs B0 and {bid})")
    run_cli(repo, "task", "add", "OXF", "--title", f"Oxford (fixes bugs B0, B1, and {bid})")
    run_cli(repo, "task", "add", "SEE", "--title", f"Fixed rows and see {bid}")
    code, out, err = run_cli(repo, "--json", "show", bid)
    assert code == 0, err
    body = json.loads(out)
    assert body["fixing"] == ["AND2", "AND3", "OXF"], body
    assert body["mentioned_by"] == ["SEE"], body


def test_the_fix_list_pattern_neither_backtracks_nor_matches_inside_a_word(repo):
    """Review of Bfc863d295f: an 'and' can end a separator or be the next element, so a
    title of many ', and's must still be answered at once; and an id embedded in a word
    (`AX`) is never a fix of X."""
    import time

    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")
    bid = _bug(repo, "the widget drops its last row", "--item", "T1", "--no-task")
    run_cli(repo, "task", "add", "MANY", "--title", "T (fixes A" + ", and" * 200 + f" ) {bid}")
    run_cli(repo, "task", "add", "WORD", "--title", f"Fixes A{bid} and {bid}x, see {bid}")
    t0 = time.monotonic()
    code, out, err = run_cli(repo, "--json", "show", bid)
    assert code == 0, err
    assert time.monotonic() - t0 < 10
    body = json.loads(out)
    assert body["fixing"] == [] and body["mentioned_by"] == ["MANY", "WORD"], body
