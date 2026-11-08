"""B50cd07e49a: a research note is not a duplicate of the bug its item fixes.

`research add --item T` was refused as a possible duplicate of the task it is recorded
for (flagged `filed_against`) AND of the bug that task fixes. The task named by --item
was already excused (`filed_against`); the BUG filed against the same item was not. A
research note beside the work item it investigates is related, not a copy: listed,
never asked -- as a research note beside a task already is. Identical text still asks.
"""

from __future__ import annotations

import json

import pytest
from conftest import run_cli

BUG_SUMMARY = (
    "the digest merge leaves fifteen derived views stale until the next rebuild regenerates them"
)


@pytest.fixture(autouse=True)
def _ask(monkeypatch):
    monkeypatch.setenv("DDFLOW_DEDUPE_ON_MATCH", "ask")
    monkeypatch.delenv("DDFLOW_DEDUPE_RESEARCH_TASK_ASKS", raising=False)


def _seed(repo):
    run_cli(repo, "init")
    run_cli(
        repo,
        "task",
        "add",
        "T-fix-stale-views",
        "--title",
        "Fix the derived views a digest merge leaves stale",
        "--body",
        BUG_SUMMARY,
    )
    run_cli(
        repo,
        "bug",
        "found",
        "--id",
        "B-stale-views",
        "--summary",
        BUG_SUMMARY,
        "--item",
        "T-fix-stale-views",
        "--no-task",
    )


def _research(repo, *extra, item="T-fix-stale-views"):
    return run_cli(
        repo,
        "--json",
        "research",
        "--question",
        "does a digest merge leave derived views stale until a rebuild",
        "--claim",
        BUG_SUMMARY,
        "--verdict",
        "CONFIRMED",
        "--probe",
        "re-render and diff",
        "--item",
        item,
        *extra,
    )


def test_a_research_note_for_a_task_is_not_refused_as_a_duplicate_of_the_bug_it_fixes(repo):
    _seed(repo)
    code, out, err = _research(repo)
    assert code == 0, (out, err)
    assert json.loads(out)["id"]


def test_the_knob_still_turns_the_ask_back_on(repo, monkeypatch):
    _seed(repo)
    monkeypatch.setenv("DDFLOW_DEDUPE_RESEARCH_TASK_ASKS", "true")
    code, _out, _err = _research(repo)
    assert code == 3


def test_a_note_not_filed_against_the_bugs_item_is_still_asked(repo):
    _seed(repo)
    code, out, _err = run_cli(
        repo,
        "--json",
        "research",
        "--question",
        BUG_SUMMARY,
        "--claim",
        BUG_SUMMARY,
        "--verdict",
        "CONFIRMED",
        "--probe",
        "re-render and diff",
    )
    assert code == 3 and "B-stale-views" in out
