"""B-extends-merges-into-open-target and B-research-extends-drops-id.

`extends X` used to MERGE the new record into X whenever X was open and unclaimed: no new
task, the new task's globs written onto a task another agent owns, and a research note
filed with `--id R-x --verdict CONFIRMED` lost both (it returned R-parent). Now `extends`
files the new record, linked to X; appending to X is the explicit answer `merge_into`
(CLI `--merge-into X`). `duplicate_of` still merges: that is what it says.
"""

from __future__ import annotations

import json

import pytest
from conftest import run_cli

from ddflow import api as A
from ddflow.api import _dedupe as DD
from ddflow.api.knowledge.research import Finding
from ddflow.core.model import fold
from ddflow.infra.log import EventLog

FIRST = "claim refuses a worktree that already exists on disk instead of adopting the worktree"
SECOND = "claim refuses an existing worktree on disk rather than adopting the worktree that exists"


@pytest.fixture(autouse=True)
def _ask(monkeypatch):
    monkeypatch.setenv("DDFLOW_DEDUPE_ON_MATCH", "ask")


def state(repo):
    return fold(EventLog(repo, "a").read_all(), strict=False)


@pytest.mark.parametrize("kind", ["task", "phase"])
def test_extends_an_open_task_files_a_new_linked_one_and_leaves_the_target(repo, kind):
    run_cli(repo, "init")
    add = A.task_add if kind == "task" else A.phase_add
    assert add(repo, "T1", title=FIRST, globs="src/a.py", agent="a").exit == 0
    out = add(
        repo, "T2", title=SECOND, globs="src/b.py",
        answer=DD.Answer("extends", "T1"), agent="b",
    )  # fmt: skip
    assert out.exit == 0 and out.data["id"] == "T2" and "extended" not in out.data, out
    st = state(repo)
    assert st.items["T1"].globs == ["src/a.py"]
    assert st.items["T2"].globs == ["src/b.py"]
    assert st.links["T2"].linked("extends") == {"T1"}


def test_merge_into_appends_to_the_open_unclaimed_target(repo):
    run_cli(repo, "init")
    assert A.task_add(repo, "T1", title=FIRST, globs="src/a.py", agent="a").exit == 0
    out = A.task_add(
        repo, "T2", title=SECOND, globs="src/b.py",
        answer=DD.Answer("merge_into", "T1"), agent="b",
    )  # fmt: skip
    assert out.exit == 0 and out.data["extended"] == "T1", out
    st = state(repo)
    assert "T2" not in st.items
    assert st.items["T1"].globs == ["src/a.py", "src/b.py"]


def test_merge_into_a_claimed_target_is_refused_and_writes_nothing(repo):
    run_cli(repo, "init")
    assert A.task_add(repo, "T1", title=FIRST, globs="src/a.py", agent="a").exit == 0
    assert A.claim(repo, "T1", no_worktree=True, agent="a").exit == 0
    out = A.task_add(
        repo, "T2", title=SECOND, globs="src/b.py",
        answer=DD.Answer("merge_into", "T1"), agent="b",
    )  # fmt: skip
    assert out.exit == 3 and "T1" in out.reason, out
    st = state(repo)
    assert "T2" not in st.items and st.items["T1"].globs == ["src/a.py"]


def test_duplicate_of_still_merges_into_an_open_task(repo):
    run_cli(repo, "init")
    A.task_add(repo, "T1", title=FIRST, globs="src/a.py", agent="a")
    out = A.task_add(repo, "T2", title=SECOND, answer=DD.Answer("duplicate_of", "T1"), agent="b")
    assert out.exit == 0 and out.data["extended"] == "T1"


def test_the_cli_has_merge_into_and_extends_files_a_new_task(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", FIRST, "--globs", "src/a.py")
    code, out, err = run_cli(
        repo, "--json", "task", "add", "T2", "--title", SECOND, "--extends", "T1"
    )
    assert code == 0, err
    assert json.loads(out)["id"] == "T2"
    code, out, err = run_cli(
        repo, "--json", "task", "add", "T3", "--title", SECOND + " again", "--merge-into", "T1"
    )
    assert code == 0, err
    assert json.loads(out)["extended"] == "T1"


def test_research_extends_keeps_the_chosen_id_and_verdict(repo):
    run_cli(repo, "init")
    base = dict(
        question="does the parser drop a trailing token", claim="it drops it",
        mechanism="off by one", falsifier="the token survives",
        probe="run it", probe_output="token lost",
    )  # fmt: skip
    assert (
        A.research_add(repo, Finding(id="R-parent", verdict="THEORETICAL", **base), agent="a").exit
        == 0
    )
    child = {**base, "question": "does the parser drop a trailing token (confirmed)"}
    out = A.research_add(
        repo,
        Finding(id="R-x", verdict="CONFIRMED", answer=DD.Answer("extends", "R-parent"), **child),
        agent="a",
    )
    assert out.exit == 0 and out.data["id"] == "R-x" and "extended" not in out.data, out
    st = state(repo)
    assert st.research["R-x"].verdict == "CONFIRMED"
    assert st.research["R-parent"].verdict == "THEORETICAL"
    assert st.links["R-x"].linked("extends") == {"R-parent"}
