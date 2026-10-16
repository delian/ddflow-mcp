"""An add answered `extends X` carries its --globs onto X (bug decision-extends-drops-globs):
unioned, reported in the output, on the API, the CLI and MCP."""

from __future__ import annotations

import json

import pytest
from conftest import run_cli

from ddflow import api as A
from ddflow.api import _dedupe as DD
from ddflow.api.decisions import Draft
from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.surfaces.mcp import Server

FIRST = "claim refuses a worktree that already exists on disk instead of adopting the worktree"
SECOND = "claim refuses an existing worktree on disk rather than adopting the worktree that exists"


@pytest.fixture(autouse=True)
def _ask(monkeypatch):
    monkeypatch.setenv("DDFLOW_DEDUPE_ON_MATCH", "ask")


def state(repo):
    return fold(EventLog(repo, "a").read_all(), strict=False)


def seed_decision(repo, globs=""):
    run_cli(repo, "init")
    A.decision_add(repo, Draft(id="D-old", title=FIRST, decision=FIRST, globs=globs), agent="a")


def test_decision_extends_unions_the_globs(repo):
    seed_decision(repo, "src/a.py")
    out = A.decision_add(
        repo,
        Draft(
            id="D-new",
            title=SECOND,
            decision=SECOND,
            globs="src/a.py,src/b.py",
            answer=DD.Answer("extends", "D-old"),
        ),
        agent="a",
    )
    assert out.exit == 0 and out.data["extended"] == "D-old"
    assert out.data["globs_added"] == ["src/b.py"]
    assert state(repo).decisions["D-old"].globs == ["src/a.py", "src/b.py"]


def test_decision_extends_onto_a_globless_decision(repo):
    seed_decision(repo)
    run_cli(
        repo, "decision", "add", "--id", "D-new", "--title", SECOND, "--decision", SECOND,
        "--globs", "src/x.py", "--extends", "D-old",
    )  # fmt: skip
    assert state(repo).decisions["D-old"].globs == ["src/x.py"]


def test_cli_says_which_globs_were_added(repo):
    seed_decision(repo)
    rc, out, err = run_cli(
        repo, "decision", "add", "--id", "D-new", "--title", SECOND, "--decision", SECOND,
        "--globs", "src/x.py", "--extends", "D-old",
    )  # fmt: skip
    assert rc == 0 and "src/x.py" in out + err


def test_mcp_decision_extends_carries_the_globs(repo):
    seed_decision(repo)
    reply = Server(repo).handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "ddflow_decision_add",
                "arguments": {
                    "id": "D-new",
                    "title": SECOND,
                    "decision": SECOND,
                    "globs": "src/m.py",
                    "relation": "extends:D-old",
                },
            },
        }
    )
    body = json.loads(reply["result"]["content"][0]["text"])
    assert body["globs_added"] == ["src/m.py"]
    assert state(repo).decisions["D-old"].globs == ["src/m.py"]


@pytest.mark.parametrize("kind", ["task", "phase"])
def test_item_extends_unions_the_globs(repo, kind):
    run_cli(repo, "init")
    add = A.task_add if kind == "task" else A.phase_add
    assert add(repo, "T1", title=FIRST, globs="src/a.py", agent="a").exit == 0
    out = add(
        repo, "T2", title=SECOND, globs="src/a.py,src/b.py",
        answer=DD.Answer("extends", "T1"), agent="a",
    )  # fmt: skip
    assert out.exit == 0 and out.data["extended"] == "T1", out
    assert out.data["globs_added"] == ["src/b.py"]
    assert state(repo).items["T1"].globs == ["src/a.py", "src/b.py"]
