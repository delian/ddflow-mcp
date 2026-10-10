"""Bug B20dc45f4c5: merge's changed-path check (`_outside_globs`) listed the landing's paths
with renames on and without `-z`. The old path of a renamed file was never listed, and a
non-ASCII name came back C-quoted (`"na\\303\\257ve.py"`), matched no glob and was reported
as out of scope although the item declared it."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from helpers import git_quiet as _git

from ddflow.api.lifecycle.merge import _outside_globs


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "r"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "old.py").write_text("x = 1\n" * 20)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    _git(repo, "checkout", "-qb", "work")
    _git(repo, "mv", "old.py", "new.py")
    (repo / "naïve.py").write_text("y = 2\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "rename and a non-ASCII name")
    return repo


def test_a_declared_non_ascii_path_is_in_scope(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    it = SimpleNamespace(globs=["old.py", "new.py", "naïve.py"])
    assert _outside_globs(repo, it, "main", "work") == []


def test_the_old_path_of_a_rename_is_a_changed_path(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    it = SimpleNamespace(globs=["new.py", "naïve.py"])
    assert _outside_globs(repo, it, "main", "work") == ["old.py"]


def test_a_listing_git_could_not_make_is_unknown_not_empty(tmp_path: Path) -> None:
    """Bug B4e42502034: a failed listing read as "nothing outside the globs"."""
    from ddflow.api.lifecycle.merge import _scope_fields

    repo = _repo(tmp_path)
    it = SimpleNamespace(globs=["new.py"])
    assert _outside_globs(repo, it, "no-such-base", "work") is None
    assert _scope_fields(None, listed=True) == {"outside_globs": [], "outside_globs_unknown": True}
    assert _scope_fields([], listed=True) == {"outside_globs": [], "outside_globs_unknown": False}


def test_an_own_worktree_landing_says_its_scope_was_not_listed() -> None:
    from ddflow.api.lifecycle.merge import _scope_fields

    assert _scope_fields([], listed=False) == {"outside_globs": [], "outside_globs_unknown": None}


def test_the_merge_result_carries_the_scope_state_on_both_paths(repo) -> None:
    """End to end (roborev on 188cf7df): an own-worktree merge says null (not listed); a
    borrowed branch says false (listed) with its outside paths."""
    import json
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from conftest import run_cli

    run_cli(repo, "init")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ddflow")
    run_cli(repo, "task", "add", "T1", "--title", "own tree", "--globs", "a.py")
    code, out, err = run_cli(repo, "--json", "claim", "T1")
    assert code == 0, out + err
    wt = Path(json.loads(out)["worktree"])
    (wt / "a.py").write_text("x = 1\n")
    _git(wt, "add", "a.py")
    _git(wt, "commit", "-qm", "a")
    code, out, err = run_cli(repo, "--json", "merge", "T1")
    assert code == 0, out + err
    body = json.loads(out)
    assert body["outside_globs"] == [] and body["outside_globs_unknown"] is None

    tree = repo.parent / "agent-tree"
    _git(repo, "worktree", "add", "-q", str(tree), "-b", "agent-work")
    run_cli(repo, "task", "add", "T2", "--title", "borrowed", "--globs", "b.py")
    code, out, err = run_cli(tree, "claim", "T2", "--no-worktree")
    assert code == 0, out + err
    for name in ("b.py", "c.py"):
        (tree / name).write_text("y = 1\n")
        _git(tree, "add", name)
        _git(tree, "commit", "-qm", name)
    code, out, err = run_cli(repo, "--json", "merge", "T2", "--branch", "agent-work")
    assert code == 0, out + err
    body = json.loads(out)
    assert body["outside_globs"] == ["c.py"] and body["outside_globs_unknown"] is False
