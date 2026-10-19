"""Bugs B099544a590, Be27952a38f, Baebce1eca7: the post-merge pins hook regenerated README.md
into the primary checkout and left it uncommitted whenever ANY other tracked file was dirty
there (ddflow's own event shards always are), so the next `ddflow merge` aborted with 'local
changes to README.md would be overwritten'. The hook folds the pin files into the merge
commit and leaves the other dirty files alone."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ddflow.config import Config
from ddflow.services import pins_merge as PM


@pytest.fixture(autouse=True)
def _outside_xdist(monkeypatch):
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)


def git(repo: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout


def _merged_repo(tmp_path: Path) -> Path:
    repo = tmp_path
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "t@e")
    git(repo, "config", "user.name", "T")
    (repo / ".ddflow").mkdir()
    (repo / ".ddflow" / "pins.toml").write_text(
        '[[pin]]\nname = "x"\nfiles = ["README.md"]\ncheck = ["sh", "-c", "grep -q new README.md"]\n'
        'regen = ["sh", "-c", "echo new > README.md"]\n'
    )
    (repo / "README.md").write_text("old\n")
    (repo / "events.jsonl").write_text("1\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "base")
    git(repo, "checkout", "-qb", "topic")
    (repo / "feature.py").write_text("x = 1\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "feature")
    git(repo, "checkout", "-q", "main")
    git(repo, "merge", "-q", "--no-ff", "-m", "merge topic", "topic")
    return repo


def test_a_pin_is_folded_into_the_merge_commit_though_another_file_is_dirty(tmp_path):
    repo = _merged_repo(tmp_path)
    (repo / "events.jsonl").write_text("1\n2\n")  # the event log, written outside the merge
    cfg = Config()
    cfg.pins.after_merge = "amend"
    said = PM.after_merge(repo, cfg)
    assert "into the merge commit" in said, said
    assert git(repo, "show", "HEAD:README.md") == "new\n"
    assert git(repo, "status", "--porcelain").strip() == "M events.jsonl"
    assert len(git(repo, "rev-list", "--parents", "-n", "1", "HEAD").split()) == 3
    assert git(repo, "diff", "--cached", "--name-only").strip() == ""


def test_a_staged_change_to_another_file_stops_the_fold(tmp_path):
    repo = _merged_repo(tmp_path)
    (repo / "events.jsonl").write_text("1\n2\n")
    git(repo, "add", "events.jsonl")
    before = git(repo, "rev-parse", "HEAD").strip()
    cfg = Config()
    cfg.pins.after_merge = "amend"
    said = PM.after_merge(repo, cfg)
    assert "not committed" in said, said
    assert git(repo, "rev-parse", "HEAD").strip() == before
    assert git(repo, "diff", "--cached", "--name-only").strip() == "events.jsonl"
