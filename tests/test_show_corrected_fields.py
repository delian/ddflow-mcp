"""`ddflow show` reads a correction back (REC-show-corrected-fields).

`ddflow correct` writes a `record.corrected` event; the verb a person uses to confirm it is
`show`. Falsifier: after a correction the new value is absent from `show`, or a corrected
field is shown without its marker (corrected, by whom, when, why). Old and new values appear
only when the digest knobs are on, and never after a `--secret` correction.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.infra.log import EventLog
from ddflow.services import approval as AP
from ddflow.surfaces.mcp import Server

SECRET = "sk-abcdefghijklmnop1234567890"


@pytest.fixture(autouse=True)
def _a_person(monkeypatch):
    for var in ("DDFLOW_AGENT", *AP.HARNESS_MARKERS):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def proj(repo):
    run_cli(repo, "init")
    log = EventLog(repo, "alice")
    log.append("task.added", "T1", {"title": "t", "body": "first body"})
    log.append("item.completed", "T1", {"author_model": "model-wrong"})
    log.append("bug.found", "B1", {"summary": "wrong claim", "title": "bt"})
    return repo


def _correct(repo, rid, field, text, reason, *extra, agent=""):
    code, out, err = run_cli(
        repo,
        "correct",
        rid,
        "--field",
        field,
        "--text",
        text,
        "--reason",
        reason,
        *extra,
        agent=agent,
    )
    assert code == 0, err
    return out


def _show(repo, *args):
    code, out, err = run_cli(repo, "show", *args)
    assert code == 0, err
    return out


def test_show_prints_author_model_and_the_corrected_marker(proj):
    assert "author model model-wrong" in _show(proj, "T1")
    _correct(proj, "T1", "author_model", "model-right", "recorded under the wrong model")
    out = _show(proj, "T1")
    assert "author model model-right" in out
    assert "model-wrong" not in out
    marker = next(ln for ln in out.splitlines() if "corrected author_model" in ln)
    assert "recorded under the wrong model" in marker and "by " in marker


def test_an_uncorrected_task_shows_no_marker(proj):
    assert "corrected" not in _show(proj, "T1")


def test_a_secret_correction_shows_the_marker_and_no_values(proj):
    """A correction marker never carries a value, and after `--secret` not even a digest of the
    old or the new one. (The record's own body is its current text, as before.)"""
    new_text = "rotated-value-9931"
    _correct(proj, "T1", "body", new_text, "pasted a key", "--secret")
    for args in ((), ("--history",)):
        out = _show(proj, "T1", *args)
        marks = [ln for ln in out.splitlines() if "corrected" in ln or "by " in ln]
        assert any("corrected body" in ln for ln in marks) and "pasted a key" in out
        assert not any(new_text in ln or "first body" in ln for ln in marks)
        assert SECRET not in out and "first body" not in out
    data = json.loads(_show(proj, "T1", "--json"))
    assert new_text not in json.dumps(data["corrections"])
    rows = data["corrections"][0]["fields"]
    assert rows and rows[0]["field"] == "body"
    assert all(r["old"] == "" and r["new"] == "" for r in rows)


def test_digests_are_off_by_default_and_on_with_the_knob(proj):
    _correct(proj, "T1", "body", "second body text", "default knobs")
    rows = json.loads(_show(proj, "T1", "--json"))["corrections"][0]["fields"]
    assert rows and rows[0]["old"] == "" and rows[0]["new"] == ""
    with (proj / ".ddflow" / "config.toml").open("a") as f:
        f.write("\n[records]\ncorrect_digest_new = true\ncorrect_min_digest_chars = 1\n")
    _correct(proj, "T1", "body", "third body text", "knob on")
    data = json.loads(_show(proj, "T1", "--json"))
    row = data["corrections"][1]["fields"][0]
    assert row["field"] == "body" and row["new"] and not row["old"]
    assert "third body text" not in json.dumps(data["corrections"])
    assert "(new " in _show(proj, "T1", "--history")


def test_history_lists_every_correction_in_order(proj):
    _correct(proj, "T1", "body", "second", "first fix")
    _correct(proj, "T1", "body", "third", "second fix")
    out = _show(proj, "T1", "--history")
    assert out.index("first fix") < out.index("second fix")
    plain_out = _show(proj, "T1")
    assert "second fix" in plain_out and "first fix" not in plain_out


def test_json_adds_fields_and_keeps_the_existing_ones(proj):
    before = json.loads(_show(proj, "T1", "--json"))
    _correct(proj, "T1", "body", "second", "why")
    after = json.loads(_show(proj, "T1", "--json"))
    assert set(before) <= set(after)
    assert set(after) - set(before) >= {"corrections"}
    assert after["author_model"] == "model-wrong"
    assert after["body"] == "second"
    entry = after["corrections"][0]
    assert entry["by"] and entry["at"] and entry["reason"] == "why"


def test_a_person_and_an_agent_both_show_by(proj):
    _correct(proj, "T1", "body", "by person", "person fix")
    # an agent holding no lease corrects a bug it authored itself
    EventLog(proj, "agent-x").append("bug.found", "B2", {"summary": "mine", "title": "t"})
    _correct(proj, "B2", "summary", "mine fixed", "agent fix", agent="agent-x")
    t1 = json.loads(_show(proj, "T1", "--json"))["corrections"][0]
    b2 = json.loads(_show(proj, "B2", "--json"))["corrections"][0]
    assert t1["by"] != b2["by"] and b2["by"] == "agent-x"
    assert "by agent-x" in _show(proj, "B2")


def test_a_bug_shows_its_correction_marker(proj):
    _correct(proj, "B1", "summary", "right claim", "typo")
    out = _show(proj, "B1")
    assert "right claim" in out and "corrected summary" in out and "typo" in out


def test_the_mcp_show_carries_the_same_fields(proj):
    _correct(proj, "T1", "author_model", "model-right", "fix")
    reply = Server(proj, agent="operator").handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "ddflow_show", "arguments": {"id": "T1"}},
        }
    )
    body = json.loads(reply["result"]["content"][0]["text"])
    assert body["author_model"] == "model-right"
    entry = body["corrections"][0]
    assert entry["reason"] == "fix" and entry["by"] and entry["at"]
    assert entry["fields"] and entry["fields"][0]["field"] == "author_model"
