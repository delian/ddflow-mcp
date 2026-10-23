"""B-complete-different-tree-note-lists-main-drift: `complete` named main's drift.

The landing commit holds everything main gained since the gate ran, so "unit_tests passed on a
different tree ... differs in .github/workflows/ci.yml ... (+106 more)" listed files the item
never touched. Given the item's own paths, only those can make the note."""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import run_cli
from helpers import git as _git

from ddflow.config import Config
from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.services import gates as G
from ddflow.services.gates import outcomes


@pytest.fixture
def adopted(repo: Path) -> Path:
    run_cli(repo, "init")
    (repo / "a.py").write_text("a = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ddflow")
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")
    EventLog(repo, "agent-x").append(
        "gate.passed",
        "T1",
        {
            "gate": "unit_tests",
            "by": "agent-x",
            "reason": "",
            "evidence": {"command": "true", "exit": 0, "tree_sha": G.tree_identity(repo)},
        },
    )
    return repo


def _stale(repo: Path, head: str, own=None):
    st = fold(EventLog(repo).read_all(), strict=False)
    return outcomes.stale_evidence_detail(st, Config.load(repo), "T1", repo, landed=head, own=own)


def _commit(repo: Path, name: str, text: str) -> str:
    (repo / name).write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", f"edit {name}")
    return _git(repo, "rev-parse", "HEAD").strip()


def test_files_main_gained_after_the_gate_ran_are_not_a_different_tree(adopted):
    head = _commit(adopted, "drift.txt", "main moved\n")
    assert [n.gate for n in _stale(adopted, head)] == ["unit_tests"], "without own paths: as before"
    assert _stale(adopted, head, own=["a.py"]) == []
    assert _stale(adopted, head, own=[]) == [], "an item with no changes of its own"


def test_an_edit_to_the_items_own_file_after_the_gate_still_is(adopted):
    _commit(adopted, "drift.txt", "main moved\n")
    head = _commit(adopted, "a.py", "a = 2\n")
    (note,) = _stale(adopted, head, own=["a.py"])
    assert note.gate == "unit_tests" and "a.py" in note.why and "drift.txt" not in note.why
