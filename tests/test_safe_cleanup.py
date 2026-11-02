"""B-e5e7dc7e27: the cleanup helper refuses unsafe targets and deletes nothing."""

from __future__ import annotations

from pathlib import Path

import pytest
from safe_cleanup import remove_owned


@pytest.fixture(name="sandbox")
def sandbox_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """A throwaway cwd with a sentinel (never the real repo) and a separate owned root."""
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    (cwd / "sentinel.txt").write_text("keep", "utf-8")
    owned = tmp_path / "owned"
    owned.mkdir()
    monkeypatch.chdir(cwd)
    return cwd, owned


@pytest.mark.parametrize("bad", ["", ".", "sub/dir", "..", None])
def test_empty_dot_and_relative_targets_are_refused(sandbox, bad) -> None:
    cwd, owned = sandbox
    with pytest.raises(ValueError):
        remove_owned(bad, owned)
    assert (cwd / "sentinel.txt").exists()


def test_a_target_outside_the_owned_root_or_the_root_itself_is_refused(sandbox) -> None:
    cwd, owned = sandbox
    for bad in (cwd, owned, owned.parent):
        with pytest.raises(ValueError):
            remove_owned(bad, owned)
    assert (cwd / "sentinel.txt").exists() and owned.exists()


def test_a_directory_inside_the_owned_root_is_removed(sandbox) -> None:
    _, owned = sandbox
    (owned / "d").mkdir()
    (owned / "d" / "f").write_text("x", "utf-8")
    remove_owned(str(owned / "d"), owned)
    assert not (owned / "d").exists()


def test_a_symlink_is_dropped_without_following_it(sandbox) -> None:
    _, owned = sandbox
    (owned / "important").mkdir()
    (owned / "link").symlink_to(owned / "important", target_is_directory=True)
    remove_owned(owned / "link", owned)
    assert (owned / "important").is_dir() and not (owned / "link").is_symlink()
