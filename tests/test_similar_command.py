"""`ddflow similar` / `ddflow_similar` (B-similar-command): read-only candidates for a
text before it is filed. The engine's own accuracy is tests/test_similar.py."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.surfaces.mcp import TOOLS, Server

BUG = (
    "claim refuses a worktree that already exists on disk instead of adopting the existing worktree"
)


def _filed(repo: Path) -> str:
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T-adopt", "--title", "adopt the existing worktree on claim")
    code, out, err = run_cli(repo, "bug", "found", "--summary", BUG, "--item", "T-adopt")
    assert code == 0, err
    run_cli(repo, "task", "add", "T-other", "--title", "render the board as markdown tables")
    return out


def _mcp(repo: Path, **args):
    reply = Server(repo).handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "ddflow_similar", "arguments": args},
        }
    )
    text = reply["result"]["content"][0]["text"]
    return json.loads(text[min(i for i in (text.find("["), text.find("{")) if i != -1) :])


def test_a_text_like_filed_records_lists_them_across_kinds(repo):
    _filed(repo)
    code, out, _ = run_cli(repo, "--json", "similar", "claim refuses existing worktree adopting")
    assert code == 0
    rows = json.loads(out)
    kinds = {r["kind"] for r in rows}
    assert {"bug", "task"} <= kinds, rows  # a bug sees the task, and vice versa
    for r in rows:
        assert set(r) == {"id", "kind", "title", "state", "score", "shared", "flags"}
        assert 0 < r["score"] <= 1 and r["shared"]
    assert all(r["id"] != "T-other" for r in rows)
    assert {r["state"] for r in rows if r["kind"] == "task"} == {"open"}
    assert {r["state"] for r in rows if r["kind"] == "bug"} == {"open"}
    assert "worktree" in rows[0]["shared"]


def test_identical_text_and_a_named_id_are_flagged(repo):
    _filed(repo)
    _c, out, _e = run_cli(repo, "--json", "similar", BUG)
    assert any("identical" in r["flags"] for r in json.loads(out))
    _c, out, _e = run_cli(repo, "--json", "similar", "something unrelated about T-other")
    named = [r for r in json.loads(out) if "named" in r["flags"]]
    assert [r["id"] for r in named] == ["T-other"]


def test_state_follows_the_record(repo):
    _filed(repo)
    _c, out, _e = run_cli(repo, "--json", "similar", BUG, "--kind", "bug")
    bug_id = json.loads(out)[0]["id"]
    run_cli(
        repo,
        "bug",
        "invalid",
        bug_id,
        "--reason",
        "not a defect",
        "--evidence",
        "probe showed adoption works",
    )
    _c, out, _e = run_cli(repo, "--json", "similar", BUG, "--kind", "bug")
    assert json.loads(out)[0]["state"] == "invalid"
    code, out, _ = run_cli(repo, "claim", "T-adopt", agent="someone")
    assert code == 0, out
    _c, out, _e = run_cli(repo, "--json", "similar", BUG, "--kind", "task")
    assert json.loads(out)[0]["state"].startswith("claimed by someone")


def test_kind_narrows_and_an_unknown_kind_is_refused(repo):
    _filed(repo)
    _c, out, _e = run_cli(repo, "--json", "similar", BUG, "--kind", "task")
    assert {r["kind"] for r in json.loads(out)} == {"task"}
    code, out, err = run_cli(repo, "similar", BUG, "--kind", "nonsense")
    assert code == 1 and "unknown kind" in (out + err)


def test_exit_0_with_candidates_and_2_with_none_and_the_human_view(repo):
    _filed(repo)
    code, out, _ = run_cli(repo, "similar", BUG)
    assert code == 0 and "shares:" in out and "T-adopt" in out
    code, out, _ = run_cli(repo, "similar", "zzyzx quuxlet frobnicate")
    assert code == 2 and "scores" in out
    code, out, _ = run_cli(repo, "--json", "similar", "zzyzx quuxlet frobnicate")
    assert code == 2 and json.loads(out) == []


def test_it_still_answers_when_the_add_check_is_off(repo):
    _filed(repo)
    run_cli(repo, "config", "dedupe.on_match", "off")
    code, out, _ = run_cli(repo, "--json", "similar", BUG)
    assert code == 0 and json.loads(out)


def test_the_cli_and_the_mcp_tool_give_the_same_answer(repo):
    _filed(repo)
    for text, extra in ((BUG, []), ("zzyzx quuxlet", []), (BUG, ["--kind", "task"])):
        _c, out, _e = run_cli(repo, "--json", "similar", text, *extra)
        args = {"text": text, **({"kind": extra[1]} if extra else {})}
        assert _mcp(repo, **args) == json.loads(out)


def test_the_tool_is_listed_and_read_only():
    assert "ddflow_similar" in TOOLS
    assert "IS THIS ALREADY FILED" in TOOLS["ddflow_similar"]["description"]


def test_kind_narrows_a_named_record_too(repo):
    """assess() lists a record the text names whatever its kind; `--kind task` must not
    hand back the bug it names (found by review of the first version)."""
    _filed(repo)
    _c, out, _e = run_cli(repo, "--json", "similar", BUG, "--kind", "bug")
    bug_id = json.loads(out)[0]["id"]
    text = f"does this repeat {bug_id} somehow"
    _c, out, _e = run_cli(repo, "--json", "similar", text, "--kind", "task")
    assert all(r["kind"] == "task" for r in json.loads(out))
    _c, out, _e = run_cli(repo, "--json", "similar", text)
    assert any(r["id"] == bug_id and "named" in r["flags"] for r in json.loads(out))
