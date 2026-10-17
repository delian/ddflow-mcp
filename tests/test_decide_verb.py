"""Decide tasks ignore inherited phase gates, and `ddflow decide` closes one from its
record: writes the document, lands it, completes (DX-decide-claim)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.core.autoorder import is_decide
from ddflow.core.model import fold
from ddflow.core.schedule import inherited_deps
from ddflow.infra.log import EventLog
from ddflow.services import approval as AP


def _git(where: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(where), *args], check=True, capture_output=True, text=True
    ).stdout


def _state(repo):
    return fold(EventLog(repo, "probe").read_all(), strict=False)


@pytest.fixture(autouse=True)
def _a_person(monkeypatch):
    for var in ("DDFLOW_AGENT", *AP.HARNESS_MARKERS):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def proj(repo):
    run_cli(repo, "init")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ddflow")
    return repo


def _queue(repo, tags: str):
    run_cli(repo, "phase", "add", "P-gate", "--title", "gate")
    run_cli(repo, "task", "add", "G1", "--title", "g", "--globs", "src/g.py")
    run_cli(repo, "phase", "add", "P-next", "--title", "next", "--needs", "P-gate")
    code, _o, err = run_cli(
        repo, "task", "add", "X-decide", "--title", "x", "--phase", "P-next",
        "--globs", "docs/ddflow/decisions/x.md", "--tags", tags,
    )  # fmt: skip
    assert code == 0, err


def test_a_decide_task_does_not_inherit_its_phases_needs(proj):
    _queue(proj, "decide")
    st = _state(proj)
    assert inherited_deps(st, st.items["X-decide"]) == []


def test_other_tasks_still_inherit_them(proj):
    _queue(proj, "docs")
    st = _state(proj)
    assert ("P-next", "P-gate") in inherited_deps(st, st.items["X-decide"])


def test_decide_writes_lands_and_completes(proj):
    _queue(proj, "decide")
    run_cli(
        proj, "decision", "add", "--id", "D-x-r2", "--title", "Pick x",
        "--decision", "Use x.", "--context", "Why.", "--new",
    )  # fmt: skip
    assert run_cli(proj, "chore", "approve")[0] == 0
    assert run_cli(proj, "claim", "X-decide")[0] == 0
    code, out, err = run_cli(
        proj, "--json", "decide", "X-decide", "--decision-id", "D-x-r2", "--model", "claude"
    )
    assert code == 0, err
    assert json.loads(out)["path"] == "docs/ddflow/decisions/x.md"
    assert _state(proj).items["X-decide"].state == "done"
    doc = _git(proj, "show", "HEAD:docs/ddflow/decisions/x.md")
    assert "## D-x-r2: Pick x" in doc and "**Decision.** Use x." in doc


def test_decide_refuses_an_unrecorded_decision(proj):
    _queue(proj, "decide")
    assert run_cli(proj, "claim", "X-decide")[0] == 0
    code, _out, err = run_cli(proj, "decide", "X-decide", "--decision-id", "D-none")
    assert code != 0 and "D-none" in err
    assert _state(proj).items["X-decide"].state != "done"


def test_decide_refuses_a_task_that_is_not_a_decide_task(proj):
    _queue(proj, "docs")
    code, _out, err = run_cli(proj, "decide", "X-decide", "--decision-id", "D-none")
    assert code == 3 and "not a decide task" in err


def test_decide_tag_is_matched_ignoring_case_and_padding(proj):
    _queue(proj, "Decide")
    st = _state(proj)
    assert inherited_deps(st, st.items["X-decide"]) == []


def test_an_unapproved_chore_list_commits_nothing(proj):
    _queue(proj, "decide")
    run_cli(proj, "decision", "add", "--id", "D-x", "--title", "t", "--decision", "d", "--new")
    assert run_cli(proj, "claim", "X-decide")[0] == 0
    code, _out, err = run_cli(proj, "decide", "X-decide", "--decision-id", "D-x")
    assert code == 3 and "chore approve" in err
    wt = Path(_state(proj).items["X-decide"].worktree)
    wt = wt if wt.is_absolute() else (proj / wt).resolve()
    assert not (wt / "docs/ddflow/decisions/x.md").exists()


def test_is_decide_strips_padding_and_case():
    assert is_decide(["task", "decide "]) and is_decide([" Decision"]) and not is_decide(["docs"])


def test_decide_outside_the_chore_globs_is_refused(proj):
    _queue(proj, "decide")
    run_cli(proj, "decision", "add", "--id", "D-x", "--title", "t", "--decision", "d", "--new")
    assert run_cli(proj, "claim", "X-decide")[0] == 0
    run_cli(proj, "update", "X-decide", "--globs", "notes/x.md")
    code, _out, err = run_cli(
        proj, "decide", "X-decide", "--decision-id", "D-x", "--path", "notes/x.md"
    )
    assert code == 3 and "[chore].globs" in err


def test_decide_prints_what_landed(proj):
    _queue(proj, "decide")
    run_cli(proj, "decision", "add", "--id", "D-x", "--title", "t", "--decision", "d", "--new")
    assert run_cli(proj, "chore", "approve")[0] == 0
    assert run_cli(proj, "claim", "X-decide")[0] == 0
    code, out, err = run_cli(proj, "decide", "X-decide", "--decision-id", "D-x")
    assert code == 0, err
    assert "X-decide decided by D-x" in out
