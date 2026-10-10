"""The five largest MCP reads are bounded (B-mcp-payload-bound).

Measured on this repository over the real MCP path: progress 150 KB, recall 49 KB,
decision_list 44 KB, next 39 KB, show 9-11 KB. The counts stay exact, a cut says so, and
the CLI's `--json` is the whole body (`ddflow/surfaces/mcp_bound.py`).
"""

from __future__ import annotations

import json

from conftest import run_cli
from helpers import tool_json as _body

from ddflow.infra.log import EventLog
from ddflow.surfaces import mcp_bound as B


def _call(repo, name: str, **arguments):
    from ddflow.surfaces.mcp import Server

    r = Server(repo, agent="agent-test").handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
    )["result"]
    assert not r.get("isError"), r
    return r


def _size(r) -> int:
    return sum(len(c["text"]) for c in r["content"])


def _blocked_queue(repo, n: int = 40) -> None:
    run_cli(repo, "init")
    log = EventLog(repo, "agent-test")
    log.append("task.added", "READY", {"title": "start here", "globs": ["r.py"]})
    for i in range(n):
        # a dependency that is not done: blocked on `deps`, with a detail sentence each
        log.append("task.added", f"W{i:02d}", {"title": f"waits {i}", "needs": ["READY"]})


def test_next_cuts_the_blocked_list_and_counts_it_exactly(repo):
    _blocked_queue(repo)
    r = _call(repo, "ddflow_next")
    body = _body(r)
    assert [i["id"] for i in body["ready"]] == ["READY"]
    assert len(body["blocked"]) == B.NEXT_BLOCKED_SHOWN
    cut = body["truncated"]
    assert cut["blocked"] == 40 and cut["shown"] == B.NEXT_BLOCKED_SHOWN
    assert sum(cut["blocked_by_reason"].values()) == 40
    assert "--json next" in cut["all"]


def test_next_is_whole_below_the_bound_and_the_cli_is_never_cut(repo):
    _blocked_queue(repo, 3)
    assert "truncated" not in _body(_call(repo, "ddflow_next"))
    _blocked_queue_cli = run_cli(repo, "--json", "next")[1]
    assert len(json.loads(_blocked_queue_cli)["blocked"]) == 3
    # and above it the CLI still lists all of them
    log = EventLog(repo, "agent-test")
    for i in range(30):
        log.append("task.added", f"X{i:02d}", {"title": f"x {i}", "needs": ["READY"]})
    cli = json.loads(run_cli(repo, "--json", "next")[1])
    assert len(cli["blocked"]) == 33
    assert len(_body(_call(repo, "ddflow_next"))["blocked"]) == B.NEXT_BLOCKED_SHOWN


def _gated(repo) -> None:
    run_cli(repo, "init")
    log = EventLog(repo, "agent-test")
    log.append("task.added", "T1", {"title": "gated", "globs": ["a.py"]})
    boiler = {
        "diff_stat": {"deletions": 0, "files": 0, "insertions": 0, "untracked": 0},
        "source_tree": "st:" + "a" * 32,
        "tree_sha": "b" * 12 + "+clean",
    }
    for g in ("research", "rules", "implement"):
        log.append(
            "gate.passed",
            "T1",
            {"gate": g, "evidence": {**boiler, "note": f"{g} " + "word " * 120}, "reason": ""},
        )


def test_show_drops_tree_boilerplate_and_cuts_long_notes_and_says_so(repo):
    _gated(repo)
    full = json.loads(run_cli(repo, "--json", "show", "T1")[1])
    assert "tree_sha" in full["gates"]["research"]["evidence"], "the CLI body is whole"
    r = _call(repo, "ddflow_show", id="T1")
    body = _body(r)
    ev = body["gates"]["research"]["evidence"]
    assert set(ev) == {"note"} and ev["note"].endswith("[...]")
    assert len(ev["note"]) <= B.TEXT_SHOWN + 6
    assert "gate" not in body["gates"]["research"]
    assert body["truncated"]["strings_cut"] == 3 and "--json show" in body["truncated"]["all"]
    assert body["state"] == "open" and body["id"] == "T1"
    assert _size(r) < len(json.dumps(full, indent=2)) / 2, (_size(r), len(json.dumps(full)))


def test_show_of_an_item_with_nothing_to_cut_says_nothing_was_cut(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")
    body = _body(_call(repo, "ddflow_show", id="T1"))
    assert "truncated" not in body and body["id"] == "T1" and body["adopted"] is False


def test_progress_is_cut_to_limit_with_a_second_block_and_limit_zero_is_all(repo):
    run_cli(repo, "init")
    log = EventLog(repo, "agent-test")
    for i in range(40):
        log.append("task.added", f"T{i:02d}", {"title": f"t {i}"})
    r = _call(repo, "ddflow_progress")
    assert len(_body(r)) == B.ROWS_SHOWN
    assert "40" in r["content"][1]["text"] and "limit=0" in r["content"][1]["text"]
    assert len(_body(_call(repo, "ddflow_progress", limit=5))) == 5
    everything = _call(repo, "ddflow_progress", limit=0)
    assert len(_body(everything)) == 40 and len(everything["content"]) == 1
    assert len(json.loads(run_cli(repo, "--json", "progress")[1])) == 40


def _decisions(repo, n: int) -> None:
    run_cli(repo, "init")
    for i in range(n):
        run_cli(
            repo,
            "decision",
            "add",
            "--id",
            f"D{i:02d}",
            "--title",
            f"decision {i}",
            "--decision",
            "chose " + "x" * 600,
            "--context",
            "because " + "y" * 600,
        )


def test_decision_list_drops_context_clips_the_text_and_cuts_to_limit(repo):
    _decisions(repo, 30)
    r = _call(repo, "ddflow_decision_list")
    rows = _body(r)
    assert len(rows) == B.ROWS_SHOWN and rows[-1]["id"] == "D29", "the newest are kept"
    assert "context" not in rows[0] and rows[0]["decision"].endswith("[...]")
    note = r["content"][1]["text"]
    assert "30" in note and "limit=0" in note and "ddflow_decision_show" in note
    whole = _body(_call(repo, "ddflow_decision_list", limit=0))
    assert len(whole) == 30
    cli = json.loads(run_cli(repo, "--json", "decision", "list")[1])
    assert len(cli) == 30 and "context" in cli[0], "the CLI body is whole"


def test_recall_drops_raw_records_and_keeps_within_budget_saying_so(repo):
    run_cli(repo, "init")
    for i in range(12):
        run_cli(
            repo,
            "decision",
            "add",
            "--id",
            f"D{i:02d}",
            "--title",
            f"payload bound {i}",
            "--decision",
            "payload bound " + "z" * 300,
        )
    r = _call(repo, "ddflow_recall", query="payload bound", limit=12, max_chars=1000)
    body = _body(r)
    hits = [h for k, v in body.items() if k != "schema" for h in v]
    assert hits and all("raw" not in h for h in hits)
    assert 1 <= len(hits) < 12
    assert "truncated" in r["content"][1]["text"] and "max_chars" in r["content"][1]["text"]
    cli = json.loads(run_cli(repo, "--json", "recall", "payload bound")[1])
    assert "raw" in next(v for k, v in cli.items() if k != "schema")[0]


def test_recall_over_mcp_keeps_one_hit_under_any_budget_and_says_it_cut(repo):
    """The budget is the API's now (tests/test_context_pack.py); over MCP a budget smaller
    than any hit still answers with the first one, and the cut is the second block."""
    run_cli(repo, "init")
    for i in range(4):
        run_cli(repo, "decision", "add", "--id", f"D{i}", "--title", f"tiny budget {i}",
                "--decision", "tiny budget " + "w" * 200)  # fmt: skip
    r = _call(repo, "ddflow_recall", query="tiny budget", limit=4, max_chars=1)
    hits = [h for k, v in _body(r).items() if k != "schema" for h in v]
    assert len(hits) == 1 and "raw" not in hits[0]
    assert "truncated: showing 1 of 4 hits within max_chars=1" in r["content"][1]["text"]


def test_mcp_json_is_compact(repo):
    _gated(repo)
    text = _call(repo, "ddflow_show", id="T1")["content"][0]["text"]
    assert "\n" not in text and '": ' not in text


def test_recall_always_says_it_left_the_raw_records_out(repo):
    run_cli(repo, "init")
    run_cli(repo, "decision", "add", "--id", "D1", "--title", "payload bound", "--decision", "x")
    r = _call(repo, "ddflow_recall", query="payload bound")
    assert "raw" in r["content"][1]["text"] and "truncated" not in r["content"][1]["text"]


def test_a_negative_limit_does_not_lift_the_bound(repo):
    run_cli(repo, "init")
    log = EventLog(repo, "agent-test")
    for i in range(40):
        log.append("task.added", f"T{i:02d}", {"title": f"t {i}"})
    assert len(_body(_call(repo, "ddflow_progress", limit=-1))) == B.ROWS_SHOWN


def test_the_reason_stays_the_second_block_and_the_cut_note_follows_it(repo):
    run_cli(repo, "init")
    blocks = [c["text"] for c in _call(repo, "ddflow_recall", query="nothing like this")["content"]]
    assert blocks[1].startswith("Nothing recalled"), blocks[1][:80]
    assert "raw record" in blocks[2], blocks[2]


def test_a_gate_timestamp_loses_only_a_utc_fraction():
    def at(stamp):
        body = {"state": "open", "gates": {"g": {"outcome": "passed", "at": stamp}}}
        return B.bound_show(body, {})[0]["gates"]["g"]["at"]

    assert at("2026-10-01T21:29:42.425803Z") == "2026-10-01T21:29:42Z"
    assert at("2026-10-01T21:29:42Z") == "2026-10-01T21:29:42Z"
    assert at("2026-10-01T21:29:42.5+05:00") == "2026-10-01T21:29:42.5+05:00"


def test_recall_bound_only_drops_the_raw_records():
    """The budget moved into the API's context pack (tests/test_context_pack.py); the bound
    keeps every hit it is given and says it left the raw records out."""
    hit = {"id": 1, "kind": "K", "headline": "h", "body": "b" * 300, "raw": {"x": "y" * 900}}
    body = {"a": [dict(hit), dict(hit)], "b": [dict(hit)]}
    out, note = B.bound_recall(body, {"max_chars": 1})
    assert [len(v) for v in out.values()] == [2, 1] and "raw record" in note
    assert all("raw" not in h for v in out.values() for h in v)


def test_show_names_every_rewrite_and_does_not_edit_the_callers_body():
    body = {
        "state": "open",
        "gates": {"g": {"gate": "g", "outcome": "passed", "at": "2026-01-01T00:00:00.5Z"}},
        "triage": {"g": {"f1": {"title": "T" * 300, "location": "T" * 300, "verdict": "open"}}},
    }
    snapshot = json.dumps(body)
    out, _ = B.bound_show(body, {})
    assert json.dumps(body) == snapshot
    assert "location" not in out["triage"]["g"]["f1"] and out["truncated"]["left_out"]
    assert out["gates"]["g"] == {"outcome": "passed", "at": "2026-01-01T00:00:00Z"}


def test_a_triage_record_without_location_or_title_is_not_reported_as_cut():
    for rec in ({"note": "x"}, {"verdict": "open", "title": "", "location": ""}):
        body = {"state": "open", "triage": {"g": {"f1": rec}}}
        assert B.bound_show(body, {})[0] == body, rec


def test_a_string_just_over_the_cut_is_left_whole_because_cutting_would_not_shrink_it():
    edge = "w" * (B.TEXT_SHOWN + len(B.CUT_MARK))
    out, _ = B.bound_show({"state": "open", "gates": {"g": {"evidence": {"note": edge}}}}, {})
    assert out["gates"]["g"]["evidence"]["note"] == edge
    over, _ = B.bound_show(
        {"state": "open", "gates": {"g": {"evidence": {"note": edge + "w"}}}}, {}
    )
    assert over["gates"]["g"]["evidence"]["note"].endswith(B.CUT_MARK)


def test_a_decision_text_just_over_the_clip_is_left_whole():
    edge = "d" * (B.DECISION_TEXT_SHOWN + len(B.CUT_MARK))
    rows = [{"id": "D1", "decision": edge, "context": "c", "at": "t"}]
    assert B.bound_decisions(rows, {})[0][0]["decision"] == edge
    rows[0]["decision"] = edge + "d"
    assert B.bound_decisions(rows, {})[0][0]["decision"].endswith(B.CUT_MARK)
