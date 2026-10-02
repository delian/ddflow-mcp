"""The shipped default of [dedupe].on_match is "warn" until B-add-dedupe-surfaces gives
the CLI, terminal and MCP a way to answer an ask (hotfix B-dedupe-default-warn): under
"ask" an add that looked like a duplicate was refused with no way through. When that task
flips the default back to "ask", this test flips with it."""

from __future__ import annotations

import pytest
from test_add_dedupe import REPORT, corpus_repo, filed, state  # noqa: F401

from ddflow import api as A
from ddflow.config import Config


def test_shipped_default_is_warn():
    assert Config().dedupe.on_match == "warn"


def test_default_config_does_not_refuse_a_duplicate_and_lists_the_candidate(
    filed,
    monkeypatch: pytest.MonkeyPatch,  # noqa: F811
):
    monkeypatch.delenv("DDFLOW_DEDUPE_ON_MATCH")  # the suite sets off; use the shipped default
    out = A.bug_found(filed, summary=REPORT, id="B3eda99e0fe", agent="a")
    assert out.exit == 0, out
    assert "B3eda99e0fe" in state(filed).bugs
    assert "B5d98a4da0a" in [c["id"] for c in out.data["candidates"]]
