"""Session ids and harness attribution come from the descriptors' [env] session_id."""

from __future__ import annotations

from ddflow.services import harnessreg as HR
from ddflow.services import sessions as S


import pytest


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """The suite may itself run inside an agent that sets its session variable."""
    for var in HR.session_env_vars():
        monkeypatch.delenv(var, raising=False)


def test_every_descriptor_variable_is_consulted_in_resolution_order():
    vars_ = HR.session_env_vars()
    assert vars_[0] == "DDFLOW_SESSION_ID"
    for h in HR.load():
        for v in h.env.session_id if h.env else ():
            assert v in vars_
    assert "CLAUDE_SESSION_ID" in vars_ and len(set(vars_)) == len(vars_)


def test_detect_names_the_harness_whose_variable_is_set():
    assert HR.detect({"GEMINI_SESSION_ID": "abc"}) == "gemini"
    assert HR.detect({}) == ""
    assert HR.detect({"GEMINI_SESSION_ID": " "}) == ""


def test_two_harnesses_at_once_are_not_guessed_between():
    assert HR.detect({"GEMINI_SESSION_ID": "a", "QWEN_CODE_SESSION_ID": "b"}) == ""


def test_a_non_claude_harness_variable_selects_its_open_session(repo, log, monkeypatch):
    sid = S.harness_session_id("g-1")
    log.append("session.started", sid, {"model": "", "tool": ""})
    monkeypatch.delenv("DDFLOW_SESSION_ID", raising=False)
    monkeypatch.setenv("GEMINI_SESSION_ID", "g-1")
    assert S.resolve(log) == (sid, "harness")


def test_an_implicit_session_records_the_detected_tool(repo, log, monkeypatch):
    monkeypatch.delenv("DDFLOW_SESSION_ID", raising=False)
    monkeypatch.setenv("QWEN_CODE_SESSION_ID", "q-9")
    sid, how = S.resolve(log)
    assert how == "implicit"
    started = [e for e in log.read_all() if e.kind == "session.started" and e.subject == sid]
    assert started[0].data["tool"] == "qwen"


def test_both_claude_variables_are_one_harness_not_an_ambiguity():
    assert HR.detect({"CLAUDE_CODE_SESSION_ID": "a"}) == "claude"
    assert HR.detect({"CLAUDE_CODE_SESSION_ID": "a", "CLAUDE_SESSION_ID": "b"}) == "claude"


def test_the_claude_code_variable_still_selects_its_session(repo, log, monkeypatch):
    sid = S.harness_session_id("c-1")
    log.append("session.started", sid, {"model": "", "tool": ""})
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "c-1")
    assert S.resolve(log) == (sid, "harness")
