"""Operator list views (B196): `lesson list`, `bug list --item`, decision `--since/--limit`,
and the `ddflow://bugs|decisions|sessions` resources.

`bug list` is served by the shared viewer engine, so the bug half here is about the two
things B196 added to it: the `item` filter and the regression-test/lesson columns. Lessons
were NOT a viewer kind, so they are added here as one -- which is what lets `ddflow_list`
serve `kind=lesson` over MCP without a duplicate tool (the tools/list byte budget is a
hard ratchet, and `ddflow_list` already covers the other viewers).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli


def _ok(repo, *argv):
    code, out, err = run_cli(repo, *argv)
    assert code == 0, (argv, out, err)
    return out


def _json_of(text: str):
    """The JSON body inside an MCP text block, which may carry a reason line with it."""
    start = min(i for i in (text.find("{"), text.find("[")) if i != -1)
    return json.loads(text[start:])


def _lesson(repo, lid, title, *, supersedes="", tags=""):
    args = ["lesson", "add", "--id", lid, "--title", title, "--summary", title]
    if supersedes:
        args += ["--supersedes", supersedes]
    if tags:
        args += ["--tags", tags]
    _ok(repo, *args)


# -- lesson list --------------------------------------------------------------------------


def test_lesson_list_shows_live_lessons_and_hides_superseded(repo):
    run_cli(repo, "init")
    _lesson(repo, "L1", "the old rule")
    _lesson(repo, "L2", "the new rule", supersedes="L1")
    out = _ok(repo, "lesson", "list")
    assert "L2" in out and "the new rule" in out
    assert "L1" not in out.split("live lessons only")[0], "a superseded lesson was listed"


def test_lesson_list_all_includes_the_superseded(repo):
    run_cli(repo, "init")
    _lesson(repo, "L1", "the old rule")
    _lesson(repo, "L2", "the new rule", supersedes="L1")
    out = _ok(repo, "lesson", "list", "--all")
    assert "L1" in out and "L2" in out


def test_lesson_list_tag_filters(repo):
    run_cli(repo, "init")
    _lesson(repo, "L1", "io rule", tags="io")
    _lesson(repo, "L2", "net rule", tags="net")
    out = _ok(repo, "lesson", "list", "--tag", "io")
    assert "L1" in out and "L2" not in out


def test_lesson_list_json_and_empty_is_exit_2(repo):
    run_cli(repo, "init")
    code, out, _ = run_cli(repo, "--json", "lesson", "list")
    assert code == 2, "an empty lesson list is NOTHING (2), like memory list"
    assert json.loads(out)["rows"] == []
    _lesson(repo, "L1", "a rule")
    code, out, _ = run_cli(repo, "--json", "lesson", "list")
    assert code == 0, out
    body = json.loads(out)
    assert body["record_kind"] == "lesson"
    assert [r["id"] for r in body["rows"]] == ["L1"]
    assert body["rows"][0]["state"] == "live"


def test_a_superseded_lesson_reads_superseded_in_json(repo):
    run_cli(repo, "init")
    _lesson(repo, "L1", "old")
    _lesson(repo, "L2", "new", supersedes="L1")
    body = json.loads(_ok(repo, "--json", "lesson", "list", "--all"))
    assert {r["id"]: r["state"] for r in body["rows"]} == {"L1": "superseded", "L2": "live"}


# -- bug list -----------------------------------------------------------


def test_bug_list_item_filters_to_that_item(repo):
    run_cli(repo, "init")
    run_cli(repo, "phase", "add", "P1", "--title", "One")
    run_cli(repo, "task", "add", "T1", "--phase", "P1", "--title", "One task")
    _ok(
        repo,
        "bug",
        "found",
        "--id",
        "B1",
        "--summary",
        "filed against T1",
        "--item",
        "T1",
        "--no-task",
    )
    _ok(repo, "bug", "found", "--id", "B2", "--summary", "filed against nothing", "--no-task")
    out = _ok(repo, "bug", "list", "--item", "T1", "--all")
    assert "B1" in out and "B2" not in out
    rows = json.loads(_ok(repo, "--json", "bug", "list", "--item", "T1", "--all"))["rows"]
    assert [r["id"] for r in rows] == ["B1"]
    assert rows[0]["item"] == "T1"


def test_bug_rows_carry_the_regression_test_and_lesson_columns(repo):
    """B196: `bug list` shows what guards the fix and the lesson the close recorded.

    Populated on purpose: asserting only the empty defaults would pass for a row builder
    that hardcoded `[]`/`""` for every bug (roborev on d2c05c21)."""
    run_cli(repo, "init")
    _lesson(repo, "L1", "what the fix taught")
    _ok(repo, "bug", "found", "--id", "B1", "--summary", "a bug", "--no-task")
    # `bug fixed` validates that the named test exists in a worktree of the repo, so the
    # test file has to be real before the bug can be closed.
    (repo / "tests").mkdir(exist_ok=True)
    (repo / "tests" / "test_x.py").write_text("def test_y():\n    assert True\n", "utf-8")
    _ok(
        repo,
        "bug",
        "fixed",
        "B1",
        "--regression-test",
        "tests/test_x.py::test_y",
        "--lesson",
        "L1",
    )
    row = json.loads(_ok(repo, "--json", "bug", "list", "--all"))["rows"][0]
    assert row["regression_tests"] == ["tests/test_x.py::test_y"], row
    assert row["lesson"] == "L1", row
    human = _ok(repo, "bug", "list", "--all")
    assert "lesson L1" in human and "1 test" in human, human


# -- decision list ------------------------------------------------------------------------


def test_decision_list_limit_keeps_the_newest(repo):
    run_cli(repo, "init")
    for i in (1, 2, 3):
        _ok(repo, "decision", "add", "--id", f"D{i}", "--title", f"t{i}", "--decision", f"d{i}")
    body = json.loads(_ok(repo, "--json", "decision", "list", "--limit", "2"))
    # Newest two, still oldest-first: the order a decision was made in never becomes a lie
    # about which ones were cut.
    assert [r["id"] for r in body] == ["D2", "D3"]


def test_decision_list_since_that_matches_nothing_is_exit_2(repo):
    run_cli(repo, "init")
    _ok(repo, "decision", "add", "--id", "D1", "--title", "t", "--decision", "d")
    code, out, _ = run_cli(repo, "--json", "decision", "list", "--since", "2999-01-01")
    assert code == 2, "a filter that empties the list is NOTHING, not success"
    assert json.loads(out) == []


def test_decision_list_since_keeps_only_recent(repo):
    run_cli(repo, "init")
    _ok(repo, "decision", "add", "--id", "D1", "--title", "old", "--decision", "d")
    body = json.loads(_ok(repo, "--json", "decision", "list", "--since", "2000-01-01"))
    assert [r["id"] for r in body] == ["D1"]


def test_mcp_decision_list_refusal_carries_its_reason_not_an_internal_error(repo):
    """The tool declares `payload: "rows"`, so `out.body("rows")` runs BEFORE a refusal is
    rendered: a refusal that omits the key is a KeyError once it crosses MCP (roborev on
    fd7ab63a). The CLI never saw it because it prints `out.reason` itself."""
    from ddflow.surfaces.mcp import Server

    run_cli(repo, "init")
    _ok(repo, "decision", "add", "--id", "D1", "--title", "t", "--decision", "d")
    srv = Server(repo)
    # The distinctive text of each refusal's REASON (not a word that also appears in the
    # JSON body, which is why a `"limit" in texts` check would pass for both arguments).
    for args, reason in (
        ({"limit": -1}, "must be 0 (all) or a positive count"),
        ({"since": "nope"}, "is not an ISO date"),
    ):
        reply = srv.handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "ddflow_decision_list", "arguments": args},
            }
        )
        assert "error" not in reply, (args, reply)  # a JSON-RPC error is the crash
        texts = " ".join(c.get("text", "") for c in reply["result"]["content"])
        assert "internal error" not in texts, (args, texts)
        assert reason in texts, (args, texts)


def test_decision_list_negative_limit_is_refused_not_a_misleading_empty(repo):
    """roborev on d2c05c21: `--limit -1` emptied the list and reported NOTHING (2), which
    reads as "no decisions match" for an argument that cannot mean that."""
    run_cli(repo, "init")
    _ok(repo, "decision", "add", "--id", "D1", "--title", "old", "--decision", "d")
    code, out, err = run_cli(repo, "decision", "list", "--limit", "-1")
    assert code == 3, (code, out, err)
    assert "limit" in out + err, (out, err)


# -- the MCP surface: ddflow_list kind=lesson, and the three resources --------------------


def test_mcp_list_serves_lessons(repo):
    from ddflow.surfaces.mcp import Server

    run_cli(repo, "init")
    _lesson(repo, "L1", "a rule")
    reply = Server(repo).handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "ddflow_list", "arguments": {"kind": "lesson"}},
        }
    )
    body = _json_of(reply["result"]["content"][0]["text"])
    assert [r["id"] for r in body["rows"]] == ["L1"]


def test_mcp_list_kind_lesson_is_no_longer_refused(repo):
    """A kind the engine does not know is REFUSED; `lesson` must no longer be one."""
    from ddflow.surfaces.mcp import Server

    run_cli(repo, "init")
    reply = Server(repo).handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "ddflow_list", "arguments": {"kind": "lesson"}},
        }
    )
    assert "unknown kind" not in reply["result"]["content"][0]["text"]


def test_bugs_md_keeps_a_lesson_with_a_pipe_or_newline_in_one_row():
    """roborev on d2c05c21: the Lesson cell was written raw, so a `|` invented a column and
    a newline split the row -- corrupting the ddflow://bugs document. The cell must be
    escaped (and folded) like every other free-text cell."""
    import re

    from ddflow.core.model import Bug, State
    from ddflow.views import markdown as md

    def pipes(line: str) -> int:
        return len(re.findall(r"(?<!\\)\|", line))

    st = State()
    st.bugs["B1"] = Bug(
        id="B1",
        title="a bug",
        item="T1",
        lesson="line one | line two\nsecond line",
        found_at="2026-04-01T00:00:00",
    )
    text = md.bugs_md(st)
    header = next(ln for ln in text.splitlines() if ln.startswith("| Bug "))
    rows = [ln for ln in text.splitlines() if ln.startswith("| B1 ")]
    assert len(rows) == 1, text  # the newline did not split the row
    assert pipes(rows[0]) == pipes(header), rows[0]
    assert "\\|" in rows[0], rows[0]


def test_the_three_resources_are_listed_and_readable(repo):
    from ddflow.surfaces.mcp import Server

    run_cli(repo, "init")
    srv = Server(repo)
    listing = srv.handle({"jsonrpc": "2.0", "id": 1, "method": "resources/list"})["result"][
        "resources"
    ]
    uris = {r["uri"] for r in listing}
    assert {"ddflow://bugs", "ddflow://decisions", "ddflow://sessions"} <= uris, uris
    for uri in ("ddflow://bugs", "ddflow://decisions", "ddflow://sessions"):
        reply = srv.handle(
            {"jsonrpc": "2.0", "id": 2, "method": "resources/read", "params": {"uri": uri}}
        )
        text = reply["result"]["contents"][0]["text"]
        assert text.strip(), f"{uri} served an empty document"
        assert reply["result"]["contents"][0]["mimeType"] == "text/markdown"
