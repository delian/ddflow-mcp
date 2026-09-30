"""`scripts/ci/pre-push` — the pre-push checks run in a scratch worktree of the pushed commit.

The bug that motivated it (B431fff0197): installed with `pre-commit install`, the checks ran
in the operator's own checkout, and pre-commit fails a hook whenever ANYTHING in the tree
changes while it runs. ddflow agents append to the committed `.ddflow/events/*.jsonl` all
the time, so the minute-long test hook failed -- "files were modified by this hook" -- with
every test green, and the push was refused.

Here the "agent" is deterministic: the hook itself appends to a tracked log in the checkout
the push starts from, which is exactly what a concurrent writer looks like to pre-commit.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "scripts" / "ci" / "pre-push"

pytestmark = pytest.mark.skipif(
    not (shutil.which("pre-commit") and shutil.which("git") and shutil.which("bash")),
    reason="needs pre-commit, git and bash",
)

# One pre-push hook that, while it runs, writes to the ORIGIN checkout the way an agent
# appending to its event log does. `fail_marker` makes it fail on purpose.
_CONFIG = """\
default_stages: [pre-push, manual]
repos:
  - repo: local
    hooks:
      - id: agent-writes-meanwhile
        name: an agent appends to its event log while this runs
        entry: sh -c 'echo "{}" >> "$ORIGIN/events.jsonl"; test ! -e fail_marker'
        language: system
        always_run: true
        pass_filenames: false
"""


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t", *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.fixture
def origin(tmp_path: Path) -> Path:
    repo = tmp_path / "origin"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / ".pre-commit-config.yaml").write_text(_CONFIG)
    (repo / "events.jsonl").write_text("")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    return repo


def _push_line(repo: Path) -> str:
    # A new branch: the remote has nothing, and HEAD is a root commit, so --all-files.
    return f"refs/heads/main {_git(repo, 'rev-parse', 'HEAD')} refs/heads/main {'0' * 40}\n"


def _env(origin: Path) -> dict[str, str]:
    return {**os.environ, "ORIGIN": str(origin), "PRE_COMMIT_HOME": str(origin.parent / "pc")}


def test_the_checks_fail_when_run_in_a_tree_someone_else_writes_to(origin):
    """The hazard itself, pinned: pre-commit run in the pushing checkout, as `pre-commit
    install` would, fails a hook that passed because the tree changed under it."""
    r = subprocess.run(
        ["pre-commit", "run", "--hook-stage", "pre-push", "--all-files"],
        cwd=origin,
        env=_env(origin),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert r.returncode == 1
    assert "files were modified by this hook" in r.stdout


def test_the_hook_checks_the_pushed_commit_in_a_scratch_worktree(origin):
    r = subprocess.run(
        ["bash", str(HOOK), "origin", "https://example.invalid/repo.git"],
        cwd=origin,
        input=_push_line(origin),
        env=_env(origin),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert r.returncode == 0, f"{r.stdout}\n{r.stderr}"
    assert "Passed" in r.stdout and "files were modified" not in r.stdout
    # The writer really did write, to the checkout the push came from.
    assert (origin / "events.jsonl").read_text() == "{}\n"
    # And the scratch worktree is gone.
    assert "ddflow-prepush" not in _git(origin, "worktree", "list")


def test_a_failing_check_refuses_the_push_and_still_cleans_up(origin):
    (origin / "fail_marker").write_text("")
    _git(origin, "add", "fail_marker")
    _git(origin, "commit", "-qm", "a commit whose checks fail")
    r = subprocess.run(
        ["bash", str(HOOK), "origin", "https://example.invalid/repo.git"],
        cwd=origin,
        input=_push_line(origin),
        env=_env(origin),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert r.returncode == 1, f"{r.stdout}\n{r.stderr}"
    assert "Failed" in r.stdout
    assert "ddflow-prepush" not in _git(origin, "worktree", "list")
