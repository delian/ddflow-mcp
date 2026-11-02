"""What an add answered `merge_into X` / `duplicate_of X` does to the target's globs, and
what `extends X` does not: appending unions the globs onto the extended record (bug
decision-extends-drops-globs), but `extends` files a NEW record linked to X -- never
writing onto a decision (B-decision-extends-mutates-old-record) -- on the API, the CLI
and MCP."""

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


def test_decision_extends_files_a_new_linked_record_and_leaves_the_target(repo):
    """B-decision-extends-mutates-old-record: `extends` on a decision must not edit the old
    record -- the new decision text and globs go on a NEW record linked to it."""
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
    assert out.exit == 0 and out.data["id"] == "D-new" and "extended" not in out.data, out
    st = state(repo)
    assert st.decisions["D-old"].globs == ["src/a.py"]
    assert st.decisions["D-new"].globs == ["src/a.py", "src/b.py"]
    assert st.links["D-new"].linked("extends") == {"D-old"}


def test_decision_extends_onto_a_globless_decision(repo):
    seed_decision(repo)
    run_cli(
        repo, "decision", "add", "--id", "D-new", "--title", SECOND, "--decision", SECOND,
        "--globs", "src/x.py", "--extends", "D-old",
    )  # fmt: skip
    st = state(repo)
    assert "D-new" in st.decisions and st.decisions["D-old"].globs == []
    assert st.decisions["D-new"].globs == ["src/x.py"]
    assert st.links["D-new"].linked("extends") == {"D-old"}


def test_cli_decision_extends_reports_the_new_record(repo):
    seed_decision(repo)
    rc, out, err = run_cli(
        repo, "decision", "add", "--id", "D-new", "--title", SECOND, "--decision", SECOND,
        "--globs", "src/x.py", "--extends", "D-old",
    )  # fmt: skip
    assert rc == 0, err
    assert "D-new" in out


def test_mcp_decision_extends_files_a_new_record_with_the_globs(repo):
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
    assert body["id"] == "D-new" and "extended" not in body
    st = state(repo)
    assert st.decisions["D-old"].globs == []
    assert st.decisions["D-new"].globs == ["src/m.py"]
    assert st.links["D-new"].linked("extends") == {"D-old"}


@pytest.mark.parametrize("kind", ["task", "phase"])
def test_item_extends_unions_the_globs(repo, kind):
    run_cli(repo, "init")
    add = A.task_add if kind == "task" else A.phase_add
    assert add(repo, "T1", title=FIRST, globs="src/a.py", agent="a").exit == 0
    out = add(
        repo, "T2", title=SECOND, globs="src/a.py,src/b.py",
        answer=DD.Answer("merge_into", "T1"), agent="a",
    )  # fmt: skip
    assert out.exit == 0 and out.data["extended"] == "T1", out
    assert out.data["globs_added"] == ["src/b.py"]
    assert state(repo).items["T1"].globs == ["src/a.py", "src/b.py"]


def test_lesson_extends_unions_the_globs(repo):
    from ddflow.api.knowledge.lessons import LessonDraft as LDraft

    run_cli(repo, "init")
    first = LDraft(id="L-old", title=FIRST, rule=FIRST, pattern="TODO", globs="src/a.py")
    assert A.lesson_add(repo, first, agent="a").exit == 0
    second = LDraft(
        id="L-new", title=SECOND, rule=SECOND, pattern="TODO", globs="src/a.py,src/b.py",
        answer=DD.Answer("extends", "L-old"),
    )  # fmt: skip
    out = A.lesson_add(repo, second, agent="a")
    assert out.exit == 0 and out.data["extended"] == "L-old", out
    assert state(repo).lessons["L-old"].globs == ["src/a.py", "src/b.py"]


def test_a_scalar_globs_field_in_the_log_is_one_glob_not_characters(repo):
    seed_decision(repo, "src/a.py")
    EventLog(repo, "a").append("record.extended", "D-old", {"text": "t", "globs": "src/s.py"})
    assert state(repo).decisions["D-old"].globs == ["src/a.py", "src/s.py"]


def test_a_patternless_lesson_takes_no_globs(repo):
    from ddflow.api.knowledge.lessons import LessonDraft as LDraft

    run_cli(repo, "init")
    assert A.lesson_add(repo, LDraft(id="L-old", title=FIRST, rule=FIRST), agent="a").exit == 0
    second = LDraft(
        id="L-new", title=SECOND, rule=SECOND, pattern="TODO", globs="src/b.py",
        answer=DD.Answer("extends", "L-old"),
    )  # fmt: skip
    out = A.lesson_add(repo, second, agent="a")
    assert out.exit == 0 and out.data["globs_added"] == []
    assert out.data["globs_ignored"] == ["src/b.py"]
    assert state(repo).lessons["L-old"].globs == []
