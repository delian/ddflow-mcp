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


def test_tests_and_docs_inside_a_code_path_are_exempt(repo, tree):
    _commit(
        tree,
        {
            "ddflow/tests/test_x.py": "x = 1\n",
            "ddflow/test_y.py": "x = 1\n",
            "ddflow/web/__tests__/h.ts": "x\n",
            "ddflow/web/Foo.test.tsx": "x\n",
            "ddflow/models/foo_spec.rb": "x\n",
            "ddflow/docs/design.md": "# design\n",
            "ddflow/docs/arch.svg": "<svg/>\n",
            "ddflow/NOTES.txt": "x\n",
            "ddflow/guide.rst": "x\n",
            "ddflow/spec/a.py": "x\n",
            "ddflow/pkg/foo_test.go": "x\n",
            "ddflow/src/FooTest.java": "x\n",
            "ddflow/widgetSpec.js": "x\n",
            "ddflow/integration_tests/h.py": "x\n",
            "ddflow/Tests/foo.py": "x\n",
        },
    )
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


def test_a_nested_readme_is_not_the_projects_readme(repo, tree):
    _commit(tree, {"ddflow/feature.py": "x = 1\n", "docs/README.md": "# docs\n"})
    assert len(_reported(repo)) == 1


def test_a_task_with_no_diff_to_read_says_the_check_could_not_run(repo):
    run_cli(repo, "init")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ddflow")
    run_cli(repo, "task", "add", "T1", "--title", "a task", "--globs", "ddflow/**")
    code, out, err = run_cli(repo, "claim", "T1", "--no-worktree")
    assert code == OK, out + err
    run_cli(repo, "config", "--set", "enforce.readme_with_code", "block")
    out = completion_verdict(repo, "T1")
    assert any("README check could not run" in w for w in out.data["warnings"])
    assert not any("README" in b for b in out.data["blockers"]), "could not run is not a block"


def test_an_unreadable_base_says_the_check_could_not_run(repo, tree):
    _commit(tree, {"ddflow/feature.py": "x = 1\n"})
    # `task add` has no --base; a recorded base that names nothing is the failure.
    _git(repo, "branch", "-m", "main", "trunk")
    run_cli(repo, "config", "--set", "enforce.readme_with_code", "block")
    out = completion_verdict(repo, "T1")
    unknown = [w for w in out.data["warnings"] if "README check could not run" in w]
    assert unknown and not any("README" in b for b in out.data["blockers"])


def test_a_branch_that_diffs_to_nothing_is_unknown_not_clean(repo):
    """Claimed --no-worktree with the branch the base itself: the diff is empty whatever
    the task did, which is "could not tell"."""
    run_cli(repo, "init")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ddflow")
    run_cli(repo, "task", "add", "T1", "--title", "a task", "--globs", "ddflow/**")
    code, out, err = run_cli(repo, "claim", "T1", "--no-worktree")
    assert code == OK, out + err
    from ddflow.services import completion as CM

    st = _load(repo)[2]
    it = st.items["T1"]
    it.branch, it.base = "main", "main"
    assert CM.changed_paths(repo, it) is None


def test_a_docs_pass_without_the_section_named_does_not_silence_it(repo, tree):
    _commit(tree, {"ddflow/feature.py": "x = 1\n"})
    run_cli(repo, "gate", "record", "T1", "docs", "--outcome", "passed", "--reason", "n/a")
    assert len(_reported(repo)) == 1
    run_cli(
        repo, "gate", "record", "T1", "docs", "--outcome", "passed", "--evidence", "README: Gates"
    )
    assert _reported(repo) == []
