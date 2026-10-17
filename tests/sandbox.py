"""A scripted-session test works in a throwaway repository, and proves it before it writes.

`--repo` resolves to whatever git says is the work tree, and a stray GIT_DIR / GIT_WORK_TREE /
GIT_INDEX_FILE in the environment (or an in-flight change) can send it to the real project
(bug sandbox-test-wrote-into-primary: a transcript run appended its events to the primary)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

GIT_ENV = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_OBJECT_DIRECTORY")


def scrub_git_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in GIT_ENV:
        monkeypatch.delenv(key, raising=False)


def assert_sandboxed(repo: Path, tmp_path: Path) -> None:
    """Refuse (AssertionError) a repo that is not under tmp_path, or whose git work tree is not
    itself, so a test never appends events or writes files into the real project."""
    real, root = repo.resolve(), tmp_path.resolve()
    assert real.is_relative_to(root), f"{real} is not under {root}: refusing to run"
    top = subprocess.run(
        ["git", "-C", str(real), "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, check=False,
    ).stdout.strip()
    assert top and Path(top).resolve() == real, (
        f"git resolves {real} to the work tree {top!r}: refusing to run"
    )
