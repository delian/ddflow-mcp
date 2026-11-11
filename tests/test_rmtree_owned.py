"""`fsio.rmtree_owned`: the guarded removal the product uses instead of `shutil.rmtree`.

`shutil.rmtree('', ignore_errors=True)` empties the current directory, and the unguarded
call at nine product sites had no protection against that. Every product removal now goes
through `fsio.rmtree_owned`, which refuses the dangerous targets outright; the import-linter
`fsio-home` contract keeps `shutil` behind `ddflow.adapters.fs` (AU-safe-rmtree).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ddflow.infra import fsio


def test_refuses_an_empty_path():
    with pytest.raises(ValueError, match="empty path"):
        fsio.rmtree_owned("")


def test_refuses_a_relative_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="relative"):
        fsio.rmtree_owned("some/relative/path")


def test_refuses_the_current_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="current directory"):
        fsio.rmtree_owned(tmp_path)


def test_refuses_a_filesystem_root():
    with pytest.raises(ValueError, match="root"):
        fsio.rmtree_owned(Path("/").anchor)


def test_removes_an_owned_directory(tmp_path):
    target = tmp_path / "owned"
    target.mkdir()
    (target / "f.txt").write_text("x", encoding="utf-8")
    (target / "sub").mkdir()
    fsio.rmtree_owned(target)
    assert not target.exists()


def test_removing_a_missing_owned_directory_is_a_noop(tmp_path):
    fsio.rmtree_owned(tmp_path / "never-existed")


def test_a_relative_path_is_refused_even_inside_the_cwd(tmp_path, monkeypatch):
    child = tmp_path / "child"
    child.mkdir()
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="relative"):
        fsio.rmtree_owned(child.name)


def test_a_symlink_to_the_cwd_is_refused(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    link = tmp_path / "link"
    link.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="current directory"):
        fsio.rmtree_owned(link)
