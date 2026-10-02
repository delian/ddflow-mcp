"""The MCP face of the add-time duplicate check (B-add-dedupe-surfaces): every add tool
takes ``relation`` and ``check_only``, and a refused add leads with its candidates and
options. What an answer does to the log is tests/test_add_dedupe.py."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.surfaces.mcp import ADD_TOOLS, TOOLS, Server

FIRST = (
    "claim refuses a worktree that already exists on disk instead of adopting the existing worktree"
)
SECOND = "claim refuses an existing worktree on disk rather than adopting the worktree that already exists there"

#: tool -> (arguments for SECOND, the id the seed was filed under)
CALLS = {
    "ddflow_task_add": ({"id": "T-new", "title": SECOND}, "T-old"),
    "ddflow_phase_add": ({"id": "P-new", "title": SECOND}, "P-old"),
    "ddflow_bug_found": ({"id": "B-new", "summary": SECOND}, "B-old"),
    "ddflow_lesson_add": ({"id": "L-new", "title": SECOND, "rule": SECOND}, "L-old"),
    "ddflow_decision_add": ({"id": "D-new", "title": SECOND, "decision": SECOND}, "D-old"),
    "ddflow_research_add": (
        {"id": "R-new", "question": SECOND, "verdict": "THEORETICAL"},
        "R-old",
    ),
    "ddflow_memory_add": ({"text": SECOND, "id": "M-new"}, "M-old"),
}
SEEDS = {
    "ddflow_task_add": {"id": "T-old", "title": FIRST},
    "ddflow_phase_add": {"id": "P-old", "title": FIRST},
    "ddflow_bug_found": {"id": "B-old", "summary": FIRST},
    "ddflow_lesson_add": {"id": "L-old", "title": FIRST, "rule": FIRST},
    "ddflow_decision_add": {"id": "D-old", "title": FIRST, "decision": FIRST},
    "ddflow_research_add": {"id": "R-old", "question": FIRST, "verdict": "THEORETICAL"},
    "ddflow_memory_add": {"text": FIRST, "id": "M-old"},
}


def call(repo: Path, tool: str, **args):
    reply = Server(repo).handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool, "arguments": args},
        }
    )
    result = reply["result"]
    return json.loads(result["content"][0]["text"]), result


@pytest.fixture
def filed_as(repo, monkeypatch):
    def make(tool: str) -> Path:
        run_cli(repo, "init")
        monkeypatch.setenv("DDFLOW_DEDUPE_ON_MATCH", "off")
        body, result = call(repo, tool, **SEEDS[tool])
        assert not result.get("isError"), body
        monkeypatch.setenv("DDFLOW_DEDUPE_ON_MATCH", "ask")
        return repo

    return make


def events(repo: Path) -> str:
    return "".join(p.read_text() for p in sorted((repo / ".ddflow" / "events").glob("*.jsonl")))


def test_every_add_tool_takes_relation_and_check_only():
    assert set(ADD_TOOLS) == set(CALLS)
    for name in ADD_TOOLS:
        props = TOOLS[name]["properties"]
        assert props["relation"][0] == "string" and props["check_only"][0] == "boolean", name


@pytest.mark.parametrize("tool", sorted(CALLS))
def test_a_refused_add_leads_with_the_candidates_and_the_options(filed_as, tool):
    repo = filed_as(tool)
    args, old = CALLS[tool]
    before = events(repo)
    body, result = call(repo, tool, **args)
    assert result["_meta"]["exit"] == 3
    assert body["refusal"]["outcome"] == "refused"
    assert "possible duplicate" in body["refusal"]["reason"]
    assert body["candidates"][0]["id"] == old
    assert {"relation": "extends", "target": old} in body["options"]
    assert {"relation": "new", "target": ""} in body["options"]
    assert events(repo) == before


@pytest.mark.parametrize("tool", sorted(CALLS))
def test_relation_extends_answers_the_refusal(filed_as, tool):
    repo = filed_as(tool)
    args, old = CALLS[tool]
    body, result = call(repo, tool, relation=f"extends:{old}", **args)
    assert result["_meta"]["exit"] == 0, body
    assert body["extended"] == old and body["relation"] == "extends"


@pytest.mark.parametrize("tool", sorted(CALLS))
def test_relation_new_files_the_record(filed_as, tool):
    repo = filed_as(tool)
    args, _old = CALLS[tool]
    body, result = call(repo, tool, relation="new", **args)
    assert result["_meta"]["exit"] == 0, body
    assert body["id"] == args.get("id", body["id"])
    assert body.get("extended") is None


def test_related_and_duplicate_of_spellings(filed_as):
    repo = filed_as("ddflow_memory_add")
    args, old = CALLS["ddflow_memory_add"]
    body, result = call(repo, "ddflow_memory_add", relation=f"related:{old}", **args)
    assert result["_meta"]["exit"] == 0
    st = fold(EventLog(repo, "a").read_all(), strict=False)
    assert st.links["M-new"].linked("related") == {"M-old"}
    body, result = call(
        repo, "ddflow_memory_add", relation=f"duplicate_of:{old}", text=SECOND + " again"
    )
    assert result["_meta"]["exit"] == 0 and body["relation"] == "duplicate_of"


@pytest.mark.parametrize("tool", sorted(CALLS))
def test_check_only_lists_candidates_and_writes_nothing(filed_as, tool):
    repo = filed_as(tool)
    args, old = CALLS[tool]
    before = events(repo)
    body, result = call(repo, tool, check_only=True, **args)
    assert result["_meta"]["exit"] == 0, body
    assert body["check_only"] is True and body["would_ask"] is True
    assert body["candidates"][0]["id"] == old
    assert events(repo) == before


def test_check_only_with_no_candidates_is_exit_2(filed_as):
    repo = filed_as("ddflow_task_add")
    before = events(repo)
    body, result = call(
        repo, "ddflow_task_add", id="T-z", title="zebra quantum marmalade", check_only=True
    )
    assert result["_meta"]["exit"] == 2
    assert body["candidates"] == []
    assert events(repo) == before


def test_a_bad_relation_is_a_failure_that_says_why(filed_as):
    repo = filed_as("ddflow_task_add")
    args, _old = CALLS["ddflow_task_add"]
    before = events(repo)
    for bad, word in (
        ("bogus:T-old", "unknown answer"),
        ("extends", "needs the id"),
        ("extends:T-nope", "no such record"),
    ):
        _body, result = call(repo, "ddflow_task_add", relation=bad, **args)
        assert result["_meta"]["exit"] == 1, bad
        assert word in result["content"][-1]["text"], bad
    assert events(repo) == before


def test_check_only_with_a_relation_is_a_failure(filed_as):
    repo = filed_as("ddflow_task_add")
    args, old = CALLS["ddflow_task_add"]
    before = events(repo)
    _body, result = call(
        repo, "ddflow_task_add", check_only=True, relation=f"extends:{old}", **args
    )
    assert result["_meta"]["exit"] == 1 and "dry run" in result["content"][-1]["text"]
    assert events(repo) == before
