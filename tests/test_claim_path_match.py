"""A staged PATH is covered by a claim only when it is inside the claim's glob
(B1997c64c5a).

`globs_overlap` answers "could two patterns share a file?" with a literal-prefix
heuristic, which is right for re-ordering claims and wrong for "is this path inside the
claim": a staged `a.md` counted as covered by a claim on `a.md.bak`, so the commit hook
accepted an edit the claim does not cover.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.core.schedule import path_in_glob


@pytest.mark.parametrize(
    "path, glob, inside",
    [
        ("a.md", "a.md", True),
        ("a.md", "a.md.bak", False),
        ("src/a.py", "src/a.pyc", False),
        ("src/ab.py", "src/a", False),
        ("src/a/x.py", "src/a", True),
        ("src/a/x.py", "src/a/", True),
        ("src/a/x/y.py", "src/*", True),
        ("src/a.py", "src/*.py", True),
        ("tests/test_x.py", "tests/test_*.py", True),
        ("x.py", "**/x.py", True),
        ("d/x.py", "**/x.py", True),
        ("src/b.py", "src/**/*.py", True),
        ("src/a/b.py", "src/**/*.py", True),
        ("a/x/b/c.py", "a/**/b/**/c.py", True),
        ("docs/a.md", "a.md", False),
        ("docs/a.md", "docs/**", True),
        ("other.md", "docs/**", False),
    ],
)
def test_path_in_glob(path, glob, inside):
    assert path_in_glob(path, glob) is inside


def _git(repo: Path, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, env=env, timeout=180
    )


def test_the_commit_hook_refuses_a_path_only_a_prefix_claim_covers(repo):
    run_cli(repo, "adopt", "--agents", "claude")
    (repo / ".ddflow" / "config.toml").write_text('[enforce]\ncommit_without_lease = "block"\n')
    (repo / "a.md").write_text("one\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "scaffold", "--no-verify")
    run_cli(repo, "phase", "add", "P1", "--title", "Core")
    run_cli(repo, "task", "add", "P1.T1", "--phase", "P1", "--globs", "a.md.bak")
    code, _out, err = run_cli(repo, "claim", "P1.T1", "--no-worktree", agent="alpha")
    assert code == 0, err  # the lease exists: the refusal below is about the path
    (repo / "a.md.bak").write_text("bak\n")
    _git(repo, "add", "a.md.bak")
    ok = _git(repo, "commit", "-m", "bak", env={**os.environ, "DDFLOW_AGENT": "alpha"})
    assert ok.returncode == 0, ok.stderr
    (repo / "a.md").write_text("two\n")
    _git(repo, "add", "a.md")
    r = _git(repo, "commit", "-m", "edit a.md", env={**os.environ, "DDFLOW_AGENT": "alpha"})
    assert r.returncode != 0, "a claim on a.md.bak let a.md be committed"
    assert "a.md" in r.stderr
