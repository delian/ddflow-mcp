"""Regression for bug sandbox-test-wrote-into-primary."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.sandbox import assert_sandboxed, scrub_git_env

ROOT = Path(__file__).resolve().parents[1]


def test_the_guard_refuses_a_repo_outside_tmp_path(tmp_path):
    with pytest.raises(AssertionError, match="refusing"):
        assert_sandboxed(ROOT, tmp_path)


def test_the_guard_refuses_a_directory_git_maps_to_another_work_tree(tmp_path):
    outer = tmp_path / "outer"
    outer.mkdir()
    subprocess.run(["git", "init", "-q", str(outer)], check=True)
    inner = outer / "plain"
    inner.mkdir()
    with pytest.raises(AssertionError, match="work tree"):
        assert_sandboxed(inner, tmp_path)


def test_the_guard_accepts_a_fresh_repo_under_tmp_path(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path / "p")], check=True)
    assert_sandboxed(tmp_path / "p", tmp_path)


def test_scrub_removes_git_redirection(monkeypatch):
    monkeypatch.setenv("GIT_DIR", "/elsewhere")
    scrub_git_env(monkeypatch)
    import os

    assert "GIT_DIR" not in os.environ


def test_the_committed_log_holds_no_shard_of_the_scripted_session():
    shards = {p.name for p in (ROOT / ".ddflow" / "events").glob("*.jsonl")}
    stray = {"tx.jsonl"} | {s for s in shards if s.startswith("pinhost-")}
    assert not (shards & stray), f"sandbox shards in the real event log: {sorted(shards & stray)}"
