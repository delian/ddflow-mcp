"""A task that changes user-visible code updates the README in the same task.

Decision D-readme-current. The per-phase `docs` gate runs once, after the work is
forgotten; this is the per-task check: `complete`, `gate status` and `brief` report a task
whose diff changed `ddflow/**` and not README.md, unless a `docs` outcome with a reason is
on record. A completion check with a severity, not a pipeline gate (see completion.py).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.api import completion_verdict
from ddflow.api._base import _load
from ddflow.infra import worktree as W

OK = 0
MESSAGE = "README not updated: record the section you changed, or `ddflow gate skip"


def _git(tree: Path, *argv: str) -> None:
    subprocess.run(["git", "-C", str(tree), *argv], check=True, capture_output=True)


@pytest.fixture
def tree(repo: Path) -> Path:
    """T1 claimed with its own worktree, forked from main."""
    run_cli(repo, "init")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ddflow")
    run_cli(repo, "task", "add", "T1", "--title", "a task", "--globs", "ddflow/**,tests/**")
    code, out, err = run_cli(repo, "claim", "T1")
    assert code == OK, out + err
    st = _load(repo)[2]
    return W.load_path(repo, st.items["T1"].worktree)


def _commit(tree: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        path = tree / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        _git(tree, "add", str(path))
    _git(tree, "commit", "-qm", "work")


def _reported(repo: Path) -> list[str]:
    out = completion_verdict(repo, "T1")
    return [w for w in out.data["warnings"] + out.data["blockers"] if "README not updated" in w]


def test_code_without_the_readme_is_reported_at_complete_and_in_gate_status(repo, tree):
    _commit(tree, {"ddflow/feature.py": "x = 1\n"})
    found = _reported(repo)
    assert len(found) == 1 and MESSAGE in found[0] and "ddflow/feature.py" in found[0]
    code, out, _ = run_cli(repo, "gate", "status", "T1")
    assert code == OK and MESSAGE in out, out
    code, out, _ = run_cli(repo, "brief", "--item", "T1")
    assert code == OK and "- docs: README not updated" in out, out


def test_a_readme_change_in_the_same_task_is_not_reported(repo, tree):
    _commit(tree, {"ddflow/feature.py": "x = 1\n", "README.md": "# proj\n\nthe feature\n"})
    assert _reported(repo) == []
    _code, out, _ = run_cli(repo, "gate", "status", "T1")
    assert "README not updated" not in out


def test_a_recorded_docs_skip_reason_is_not_reported(repo, tree):
    _commit(tree, {"ddflow/feature.py": "x = 1\n"})
    assert _reported(repo)
    code, out, err = run_cli(
        repo, "gate", "skip", "T1", "docs", "--reason", "internal refactor, nothing visible"
    )
    assert code == OK, out + err
    assert _reported(repo) == []


def test_test_only_and_docs_only_changes_are_exempt(repo, tree):
    _commit(tree, {"tests/test_x.py": "def test_x(): ...\n", "docs/guide.md": "# guide\n"})
    assert _reported(repo) == []


def test_event_log_housekeeping_is_exempt(repo, tree):
    _commit(tree, {".ddflow/events/x.jsonl": "{}\n"})
    assert _reported(repo) == []


def test_off_silences_it_and_block_refuses(repo, tree):
    _commit(tree, {"ddflow/feature.py": "x = 1\n"})
    run_cli(repo, "config", "--set", "enforce.readme_with_code", "block")
    out = completion_verdict(repo, "T1")
    assert any("README not updated" in b for b in out.data["blockers"])
    run_cli(repo, "config", "--set", "enforce.readme_with_code", "off")
    assert _reported(repo) == []


def test_the_driver_says_so():
    """The per-agent deltas restate nothing by design (every harness reads the driver)."""
    root = Path(__file__).resolve().parents[1]
    for rel in (
        "docs/ddflow/drivers/implement-phase.md",
        "ddflow/templates/drivers/implement-phase.md",
    ):
        assert "ddflow gate skip <id> docs" in (root / rel).read_text(), rel


def test_a_landed_task_is_judged_by_what_landed(repo, tree):
    """After `merge` the worktree is gone; the landed diff is what is measured."""
    _commit(tree, {"ddflow/feature.py": "x = 1\n"})
    code, out, err = run_cli(repo, "merge", "T1")
    assert code == OK, out + err
    assert not tree.exists()
    assert len(_reported(repo)) == 1
