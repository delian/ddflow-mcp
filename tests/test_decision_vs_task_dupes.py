"""B-decision-add-duplicate-vs-own-task: a decision is not a duplicate of a task.

`decision add --item T` was refused as a possible duplicate of the task a decision is
RECORDED FOR (and of its neighbours), so every decision made by a *-decide task needed
--new. The task named by --item was already excused (`filed_against`); a SIBLING task that
reads like the decision was not. A decision beside the work item it governs is related,
not a copy: listed, never asked -- as a research note beside a task is
(`[dedupe].research_task_asks`, which now covers both). Identical text still asks.
"""

from __future__ import annotations

import json

import pytest
from conftest import run_cli

TASK_TITLE = "Observe each client capability at connect time and cache the observation per client in the matrix"
TASK_BODY = "implement observation of client capability at connect time with a per client cache feeding the capability matrix"


@pytest.fixture(autouse=True)
def _ask(monkeypatch):
    monkeypatch.setenv("DDFLOW_DEDUPE_ON_MATCH", "ask")


def _seed(repo):
    run_cli(repo, "init")
    run_cli(
        repo,
        "task",
        "add",
        "SU-decide",
        "--title",
        "Decide the client capability matrix strategy",
        "--body",
        "choose how clients are observed",
    )
    run_cli(repo, "task", "add", "SU-observe", "--title", TASK_TITLE, "--body", TASK_BODY)


def _decide(
    repo,
    *extra,
    title="Observe every client capability at connect time and cache that observation per client",
    decision=TASK_BODY + " (decided)",
):
    return run_cli(
        repo, "--json", "decision", "add", "--title", title, "--decision", decision,
        "--item", "SU-decide", *extra,
    )  # fmt: skip


def test_a_decision_recorded_for_a_task_is_not_refused_as_a_duplicate_of_a_sibling_task(repo):
    _seed(repo)
    code, out, err = _decide(repo)
    assert code == 0, (out, err)
    assert json.loads(out)["id"]


def test_the_knob_still_turns_the_ask_back_on(repo, monkeypatch):
    _seed(repo)
    monkeypatch.setenv("DDFLOW_DEDUPE_RESEARCH_TASK_ASKS", "true")
    code, _out, _err = _decide(repo)
    assert code == 3


def test_an_identical_task_text_still_asks(repo):
    _seed(repo)
    run_cli(repo, "task", "add", "SU-same", "--title", "Observe the matrix", "--body", "cache it")
    code, out, _err = run_cli(
        repo, "--json", "decision", "add", "--title", "Observe the matrix", "--decision", "cache it"
    )
    assert code == 3 and "SU-same" in out
