"""The shipped default of [dedupe].on_match is "ask" again (B-add-dedupe-surfaces): every
surface -- CLI flags, the terminal prompt, MCP ``relation`` -- can answer one. It was "warn"
while none could (hotfix B-dedupe-default-warn), so an ask refused with no way through. The
file keeps its hotfix name; it pins the default and that the refusal is answerable."""

from __future__ import annotations

import pytest
from test_add_dedupe import REPORT, corpus_repo, filed, state  # noqa: F401

from ddflow import api as A
from ddflow.config import Config


def test_shipped_default_is_ask():
    assert Config().dedupe.on_match == "ask"


def test_default_config_refuses_a_duplicate_and_the_refusal_can_be_answered(
    filed,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.delenv("DDFLOW_DEDUPE_ON_MATCH")  # the suite sets it; use the shipped default
    out = A.bug_found(filed, summary=REPORT, id="B3eda99e0fe", agent="a")
    assert out.exit == 3, out
    assert out.data["candidates"][0]["id"] == "B5d98a4da0a"
    assert "B3eda99e0fe" not in state(filed).bugs
    answered = A.bug_found(
        filed, summary=REPORT, id="B3eda99e0fe", answer=A.DedupeAnswer("new"), agent="a"
    )
    assert answered.exit == 0 and "B3eda99e0fe" in state(filed).bugs
