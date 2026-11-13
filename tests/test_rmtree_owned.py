"""`fsio.rmtree_owned`: the guarded removal the product uses instead of `shutil.rmtree`.

`shutil.rmtree('', ignore_errors=True)` empties the current directory, and the unguarded
call at nine product sites had no protection against that. Every product removal now goes
through `fsio.rmtree_owned`, which refuses the dangerous targets; the import-linter
`fsio-home` contract keeps `shutil` behind `ddflow.adapters.fs` (AU-safe-rmtree).

A dangerous target is never removed. The default `ignore_errors=True` matches the
best-effort call sites (a refusal is silent, so a "never raises" cleanup stays silent);
`ignore_errors=False` raises `ValueError` so a direct caller gets the refusal loudly.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ddflow.infra import fsio


def test_refuses_an_empty_path_silently_by_default(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    marker = tmp_path / "marker.txt"
    marker.write_text("kept", encoding="utf-8")
    fsio.rmtree_owned("")  # refused-to-delete: no raise (ignore_errors=True)
    assert marker.exists()  # the cwd's children were not emptied
    assert Path.cwd() == tmp_path


def test_raises_on_dangerous_targets_when_errors_are_not_ignored():
    with pytest.raises(ValueError, match="empty path"):
        fsio.rmtree_owned("", ignore_errors=False)
    with pytest.raises(ValueError, match="relative"):
        fsio.rmtree_owned("relative/path", ignore_errors=False)
    with pytest.raises(ValueError, match="root"):
        fsio.rmtree_owned("/", ignore_errors=False)


def test_raises_on_the_current_directory_when_errors_are_not_ignored(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="current directory"):
        fsio.rmtree_owned(tmp_path, ignore_errors=False)


def test_removes_an_owned_directory(tmp_path):
    target = tmp_path / "owned"
    target.mkdir()
    (target / "f.txt").write_text("x", encoding="utf-8")
    (target / "sub").mkdir()
    fsio.rmtree_owned(target)
    assert not target.exists()


def test_removing_a_missing_owned_directory_is_a_noop(tmp_path):
    fsio.rmtree_owned(tmp_path / "never-existed")


def test_a_relative_path_is_refused_silently(tmp_path, monkeypatch):
    child = tmp_path / "child"
    child.mkdir()
    marker = child / "marker.txt"
    marker.write_text("kept", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    fsio.rmtree_owned(child.name)  # relative: refused, not removed, not raised
    assert marker.exists()


def test_a_symlink_to_the_cwd_is_refused_silently(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    link = tmp_path / "link"
    link.symlink_to(tmp_path, target_is_directory=True)
    marker = tmp_path / "marker.txt"
    marker.write_text("x", encoding="utf-8")
    fsio.rmtree_owned(link)  # refused silently: the cwd is not deleted
    assert marker.exists()
