"""`ddflow correct`: the verb and the MCP action that emit `record.corrected` (REC-correct-surface).

A wrongly recorded value is corrected with history kept, never by hand-editing an event file.
D-correction-permissions-2: an agent corrects its own records and records on an item it holds a
lease on, without asking; everything else needs the operator. The old text stays in the log.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from helpers import state as _state

from ddflow.core.handlers.asks import asks_of
from ddflow.infra.log import EventLog
from ddflow.services import approval as AP
from ddflow.surfaces.mcp import Server

SECRET = "sk-abcdefghijklmnop1234567890"
OLD = "the wrong claim zebra 4711"


@pytest.fixture(autouse=True)
def _a_person(monkeypatch):
    for var in ("DDFLOW_AGENT", *AP.HARNESS_MARKERS):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def proj(repo):
    run_cli(repo, "init")
    EventLog(repo, "alice").append("bug.found", "B1", {"summary": OLD, "title": "t"})
    EventLog(repo, "alice").append(
        "bug.found", "B2", {"summary": "other", "title": "t2", "item": "T1"}
    )
    EventLog(repo, "carol").append("task.added", "T1", {"title": "t", "body": "b"})
    return repo


def _call(repo, tool, agent="", **args):
    if agent:
        args.setdefault("as_agent", agent)
    reply = Server(repo, agent=agent or "operator").handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool, "arguments": args},
        }
    )
    res = reply["result"]
    text = res["content"][0]["text"]
    try:
        text = json.loads(text)
    except ValueError:
        pass
    return text, bool(res.get("isError")) or (isinstance(text, dict) and "refusal" in text)


def _corrections(repo):
    return [e for e in EventLog(repo).read_all() if e.kind == "record.corrected"]


def test_a_person_corrects_a_record_and_is_told_what_stays(proj):
    code, out, err = run_cli(
        proj,
        "correct",
        "B1",
        "--field",
        "summary",
        "--text",
        "the right claim",
        "--reason",
        "wrong fact",
    )
    assert code == 0, (out, err)
    assert _state(proj).bugs["B1"].summary == "the right claim"
    text = out + err
    assert "still in the event log" in text, text
    assert "scrub" in text and "rotate" in text, text
    (ev,) = _corrections(proj)
    assert ev.subject == "B1" and ev.data["kind"] == "bug" and ev.data["reason"] == "wrong fact"


def test_the_kind_is_found_from_the_id_and_a_task_body_is_corrected(proj):
    code, out, err = run_cli(
        proj, "correct", "T1", "--field", "body", "--text", "fixed", "--reason", "typo"
    )
    assert code == 0, (out, err)
    assert _state(proj).items["T1"].body == "fixed"


def test_digests_are_off_by_default_and_secret_keeps_them_off(proj):
    run_cli(proj, "correct", "B1", "--field", "summary", "--text", "new one", "--reason", "r")
    (ev,) = _corrections(proj)
    assert ev.data["digest_old"] is False and ev.data["digest_new"] is False, ev.data
    (proj / ".ddflow").mkdir(exist_ok=True)
    with (proj / ".ddflow" / "config.toml").open("a") as f:
        f.write("\n[records]\ncorrect_digest_old = true\ncorrect_digest_new = true\n")
    run_cli(proj, "correct", "B1", "--field", "summary", "--text", "two", "--reason", "r")
    run_cli(
        proj, "correct", "B1", "--field", "summary", "--text", "three", "--reason", "r", "--secret"
    )
    _a, second, third = _corrections(proj)
    assert second.data["digest_old"] is True and second.data["digest_new"] is True
    assert third.data["digest_old"] is False and third.data["digest_new"] is False, third.data


def test_an_agent_corrects_its_own_record_without_asking(proj):
    code, out, err = run_cli(
        proj,
        "correct",
        "B1",
        "--field",
        "summary",
        "--text",
        "mine now",
        "--reason",
        "typo",
        agent="alice",
    )
    assert code == 0, (out, err)
    assert _corrections(proj)[0].agent == "alice"


def test_an_agent_corrects_a_record_on_an_item_it_holds(proj):
    assert run_cli(proj, "claim", "T1", "--no-worktree", agent="bob")[0] == 0
    code, out, err = run_cli(
        proj,
        "correct",
        "B2",
        "--field",
        "summary",
        "--text",
        "fixed",
        "--reason",
        "typo",
        agent="bob",
    )
    assert code == 0, (out, err)
    # ...and a record on an item it does NOT hold is another agent's
    code, _o, err = run_cli(
        proj,
        "correct",
        "B1",
        "--field",
        "summary",
        "--text",
        "fixed",
        "--reason",
        "typo",
        agent="bob",
    )
    assert code == 3 and "--approval request" in err, err


def test_another_agents_record_needs_an_ask_and_the_approved_rerun_works(proj):
    argv = ("correct", "B1", "--field", "summary", "--text", "theirs", "--reason", "typo")
    code, _o, err = run_cli(proj, *argv, agent="bob")
    assert code == 3 and "--approval request" in err, err
    assert "ddflow correct" in err, "a refusal names the nearest verb"
    assert not _corrections(proj) and not asks_of(_state(proj))

    code, _o, err = run_cli(proj, *argv, "--approval", "request", agent="bob")
    assert code == 3
    ask_id = re.search(r"approval (Q[0-9a-f]+)", err).group(1)
    assert asks_of(_state(proj))[ask_id]["verb"] == "record.correct"

    digest = asks_of(_state(proj))[ask_id]["digest"]
    assert run_cli(proj, "approvals", "approve", ask_id, "--digest", digest[:12])[0] == 0
    code, out, err = run_cli(proj, *argv, "--approval", ask_id, agent="bob")
    assert code == 0, (out, err)
    assert _state(proj).bugs["B1"].summary == "theirs"
    assert _corrections(proj)[0].data["approval"]["ask"] == ask_id


def test_a_missing_reason_is_refused_and_writes_nothing(proj):
    code, _o, _e = run_cli(proj, "correct", "B1", "--field", "summary", "--text", "x")
    assert code != 0 and not _corrections(proj)
    out, bad = _call(
        proj, "ddflow_task", agent="alice", action="correct", id="B1", field="summary", text="x"
    )
    assert bad and "reason" in json.dumps(out), out
    out, bad = _call(
        proj,
        "ddflow_task",
        agent="alice",
        action="correct",
        id="B1",
        field="summary",
        text="x",
        reason="  ",
    )
    assert bad and not _corrections(proj), out


def test_the_new_text_is_redacted_before_it_is_written(proj):
    code, out, err = run_cli(
        proj, "correct", "B1", "--field", "summary", "--text", f"now {SECRET}", "--reason", "r"
    )
    assert code == 0, (out, err)
    assert SECRET not in _state(proj).bugs["B1"].summary
    assert not any(SECRET in line for f in (proj / ".ddflow").rglob("*.jsonl") for line in open(f))


def test_an_unknown_field_or_record_is_refused_naming_the_nearest_verb(proj):
    code, out, err = run_cli(
        proj, "correct", "B1", "--field", "nonsense", "--text", "x", "--reason", "r"
    )
    assert code != 0 and "summary" in out + err and not _corrections(proj)
    code, out, err = run_cli(
        proj, "correct", "NOPE", "--field", "body", "--text", "x", "--reason", "r"
    )
    assert code != 0 and not _corrections(proj)


def test_the_text_may_come_from_a_file(proj, tmp_path):
    f = tmp_path / "t.txt"
    f.write_text("from a file")
    code, out, err = run_cli(
        proj, "correct", "B1", "--field", "summary", "--from-file", str(f), "--reason", "r"
    )
    assert code == 0, (out, err)
    assert _state(proj).bugs["B1"].summary == "from a file"


def test_cli_and_mcp_are_one_declaration(proj):
    out, bad = _call(
        proj,
        "ddflow_task",
        agent="alice",
        action="correct",
        id="B1",
        field="summary",
        text="via mcp",
        reason="r",
    )
    assert not bad, out
    assert _state(proj).bugs["B1"].summary == "via mcp"
    out, bad = _call(
        proj, "ddflow_correct", agent="alice", id="B1", field="summary", text="old name", reason="r"
    )
    assert not bad, out
    assert _state(proj).bugs["B1"].summary == "old name"
    assert len(_corrections(proj)) == 2
    from ddflow.surfaces.tools import TOOLS
    from ddflow.surfaces.tools import families as F

    assert "ddflow_correct" in TOOLS
    assert F.FamilyAlias("ddflow_correct", "ddflow_task", "correct") in F.ALIASES


def test_an_agent_over_mcp_gets_the_same_permission_model(proj):
    out, bad = _call(
        proj,
        "ddflow_task",
        agent="bob",
        action="correct",
        id="B1",
        field="summary",
        text="x",
        reason="r",
    )
    assert bad and "approval" in json.dumps(out), out
    out, bad = _call(
        proj,
        "ddflow_task",
        agent="alice",
        action="correct",
        id="B1",
        field="summary",
        text="x",
        reason="r",
    )
    assert not bad, out


def test_the_author_model_of_a_completion_can_be_corrected(proj):
    EventLog(proj, "bob").append(
        "item.completed",
        "T1",
        {"kind": "task", "author_model": "wrong-model", "model_source": "flag"},
    )
    assert _state(proj).items["T1"].author_model == "wrong-model"
    code, out, err = run_cli(
        proj,
        "correct",
        "T1",
        "--field",
        "author_model",
        "--text",
        "right-model",
        "--reason",
        "recorded as the wrong model",
    )
    assert code == 0, (out, err)
    assert _state(proj).items["T1"].author_model == "right-model"


def test_help_and_readme_name_the_verb():
    root = Path(__file__).resolve().parents[1]
    assert "ddflow correct" in (root / "README.md").read_text()
    topics = list((root / "ddflow/templates/prompts/help").glob("*.md"))
    assert any("ddflow correct" in t.read_text() for t in topics)


def test_every_refusal_names_the_nearest_verb(proj):
    code, out, err = run_cli(
        proj, "correct", "B1", "--field", "summary", "--text", "x", "--reason", "r" * 600
    )
    assert code != 0 and "ddflow correct <id>" in out + err, out + err


def test_a_binary_file_is_a_refusal_not_a_traceback(proj, tmp_path):
    f = tmp_path / "bad.bin"
    f.write_bytes(b"\xff\xfe\x00\x80\x81")
    code, out, err = run_cli(
        proj, "correct", "B1", "--field", "summary", "--from-file", str(f), "--reason", "r"
    )
    assert code != 0 and "Traceback" not in out + err and "as text" in out + err, out + err
    assert "ddflow correct <id>" in out + err, "the refusal names the nearest verb"
    assert not _corrections(proj)


def test_the_author_is_read_from_the_selected_kind_only(proj):
    """A task and a phase of one id: the phase's author is the phase.added event's agent."""
    EventLog(proj, "bob").append("task.added", "X1", {"title": "t"})
    EventLog(proj, "alice").append("phase.added", "X1", {"title": "p"})
    code, _o, err = run_cli(
        proj,
        "correct",
        "X1",
        "--kind",
        "phase",
        "--field",
        "body",
        "--text",
        "b",
        "--reason",
        "r",
        agent="alice",
    )
    assert code == 0, err
