"""B-e47bb1cade: a gate run is superseded by changed CONTENT of the item's own paths, not by
the HEAD of a shared checkout moving under unrelated commits (other agents' merges)."""

from __future__ import annotations

from helpers import git as _git

from ddflow.services import gates as G


def _repo(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "mine.py").write_text("1")
    (tmp_path / "other.py").write_text("1")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "one")
    return G.tree_identity(tmp_path)


def test_unrelated_commit_does_not_supersede_a_run(tmp_path):
    began = _repo(tmp_path)
    (tmp_path / "other.py").write_text("2")
    _git(tmp_path, "commit", "-qam", "someone else")
    now = G.tree_identity(tmp_path)
    assert not G.superseded_by(began, now, tmp_path, paths=["mine.py"])
    assert G.superseded_by(began, now, tmp_path)  # no scope: ancestry alone, as before


def test_a_commit_touching_the_items_paths_supersedes(tmp_path):
    began = _repo(tmp_path)
    (tmp_path / "mine.py").write_text("2")
    _git(tmp_path, "commit", "-qam", "mine")
    assert G.superseded_by(began, G.tree_identity(tmp_path), tmp_path, paths=["mine.py"])


def test_a_non_ascii_path_is_matched_not_quoted(tmp_path):
    began = _repo(tmp_path)
    (tmp_path / "café.py").write_text("2")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "accent")
    assert G.superseded_by(began, G.tree_identity(tmp_path), tmp_path, paths=["*.py"])
