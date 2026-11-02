"""LK-baseline-merge-drivers: union for baselines, regenerate for pins, opt-in Mergiraf."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ddflow.config import Config
from ddflow.services import mergedrivers as MD


def _git(repo: Path, *a: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *a], check=True, capture_output=True, text=True
    ).stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "a@b.c")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / ".ddflow").mkdir()
    (tmp_path / ".ddflow" / "config.toml").write_text(
        '[pins]\nbaseline_globs = ["base/*.txt"]\n'
    )
    (tmp_path / "base").mkdir()
    (tmp_path / "base" / "a.txt").write_text("a\nm\nz\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "init")
    return tmp_path


def test_plan_shows_union_line_and_writes_nothing(repo: Path) -> None:
    p = MD.plan(repo, Config.load(repo))
    assert p.attrs == ["base/*.txt merge=union"]
    assert not (repo / ".gitattributes").exists()
    assert ".gitattributes: base/*.txt merge=union" in p.lines()


def test_apply_makes_two_branch_adds_merge_cleanly(repo: Path) -> None:
    MD.apply(repo, Config.load(repo))
    assert MD.plan(repo, Config.load(repo)).attrs == []  # idempotent
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "attrs")
    _git(repo, "checkout", "-qb", "x")
    (repo / "base" / "a.txt").write_text("a\nb\nm\nz\n")
    _git(repo, "commit", "-qam", "x")
    _git(repo, "checkout", "-q", "main")
    (repo / "base" / "a.txt").write_text("a\nm\nn\nz\n")
    _git(repo, "commit", "-qam", "main")
    _git(repo, "merge", "-q", "--no-edit", "x")  # a conflict would raise
    assert (repo / "base" / "a.txt").read_text().split() == ["a", "b", "m", "n", "z"]
    assert MD.check_baselines(repo, Config.load(repo)) == []


def test_check_baselines_names_unsorted_duplicate_and_markers(repo: Path) -> None:
    (repo / "base" / "a.txt").write_text("b\na\n")
    (repo / "base" / "b.txt").write_text("a\na\n")
    (repo / "base" / "c.txt").write_text("<<<<<<< ours\na\n")
    _git(repo, "add", "-A")
    found = MD.check_baselines(repo, Config.load(repo))
    assert len(found) == 3
    assert any("not sorted" in f for f in found)
    assert any("repeats" in f for f in found)
    assert any("conflict markers" in f for f in found)
    assert all("sort -u" in f for f in found)


def test_mergiraf_off_by_default_and_unavailable_without_binary(repo: Path, monkeypatch) -> None:
    assert MD.plan(repo, Config.load(repo)).config == {}
    (repo / ".ddflow" / "config.toml").write_text("[pins]\nmergiraf = true\n")
    monkeypatch.setattr(MD.shutil, "which", lambda _n: None)
    p = MD.plan(repo, Config.load(repo))
    assert p.config == {} and p.attrs == []
    assert any("binary" in u and "ddflow pins drivers --install" in u for u in p.unavailable)


def test_mergiraf_needs_min_git(repo: Path, monkeypatch) -> None:
    (repo / ".ddflow" / "config.toml").write_text("[pins]\nmergiraf = true\n")
    monkeypatch.setattr(MD.shutil, "which", lambda _n: "/bin/mergiraf")
    monkeypatch.setattr(MD, "_git_version", lambda _r: (2, 43))
    p = MD.plan(repo, Config.load(repo))
    assert any("2.44" in u and "2.43" in u for u in p.unavailable) and p.attrs == []
    monkeypatch.setattr(MD, "_git_version", lambda _r: (2, 45))
    p = MD.plan(repo, Config.load(repo))
    assert p.attrs == ["*.py merge=mergiraf"]
    assert p.config["mergiraf.driver"] == MD.MERGIRAF_DRIVER


def test_keep_mergiraf_needs_more_than_the_threshold() -> None:
    assert MD.keep_mergiraf(11, 100, 10)
    assert not MD.keep_mergiraf(10, 100, 10)
    assert not MD.keep_mergiraf(0, 0, 10)
