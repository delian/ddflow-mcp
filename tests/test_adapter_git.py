"""The `Git` port: the same cases against `FakeGit` (temp-repo backed) and the real
module (`ddflow.adapters.git`).

`CONFORMANCE` is read by the generic contract test (tests/test_ports_contract.py).
"""

from __future__ import annotations

from ddflow.adapters import git as real_git
from ddflow.adapters.fakes.git import FakeGit, TempRepo


def git_cases(io) -> None:
    """Every verb the port declares, run against a repository io answers for.

    `io` is either the real module (a throwaway `TempRepo` is stood up here) or a
    `FakeGit` whose own `TempRepo` is used, so the SAME cases run against both.
    """
    tmp = getattr(io, "temp", None)
    own = tmp is None
    if own:
        tmp = TempRepo()
    root = tmp.path
    try:
        init = io.head_commit(root)
        assert init, "the temp repo carries one commit"
        assert io.commit_of(root, "HEAD") == init
        ran = io.run(root, "rev-parse", "--verify", "HEAD")
        assert ran.ok and ran.out == init

        tracked = io.files(root, "tracked")
        assert tracked is not None and "README.md" in tracked
        assert io.status(root) == []
        assert io.unmerged(root) == []

        tmp.write("a.txt", "one\n")
        second = tmp.commit("second")
        assert second and second != init
        assert "a.txt" in io.changed_paths(root, init, second)
        assert io.commits_ahead(root, init, second) == 1
        assert io.is_ancestor(root, init, second) is True
        assert io.is_ancestor(root, second, init) is False
        assert io.commits_missing_from(root, [second], init) == {second}

        ahead = io.branch_tip_aheads(root, init)
        assert isinstance(ahead, dict) and list(ahead.values()) == [1]

        dest = root / "x.bundle"
        io.bundle_create(root, dest, "main")
        assert "refs/heads/main" in io.bundle_heads(dest)
    finally:
        if own:
            tmp.remove()


CONFORMANCE = {
    "Git": {
        "real": lambda monkeypatch: real_git,
        "cases": git_cases,
        "fake": lambda cls: cls(),
    }
}


def test_the_fake_is_backed_by_a_temp_repository():
    io = FakeGit()
    assert io.head_commit(io.repo), "FakeGit owns a repository with a commit"
    io.temp.write("b.txt", "two\n")
    io.temp.commit("third")
    assert "b.txt" in io.files(io.repo, "tracked")
    assert io.calls and io.calls[0][0] in {"head_commit", "files"}, "verbs are recorded"
    io.temp.remove()


def test_a_temp_repo_can_be_shared_with_the_real_module():
    tmp = TempRepo()
    try:
        tmp.write("c.txt", "three\n")
        sha = tmp.commit("fourth")
        assert "c.txt" in real_git.files(tmp.path, "tracked")
        assert real_git.commit_of(tmp.path, "HEAD") == sha
        assert sha == real_git.head_commit(tmp.path)
    finally:
        tmp.remove()


def test_temp_repo_remove_deletes_the_repository():
    tmp = TempRepo()
    root = tmp.path
    assert root.is_dir()
    tmp.remove()
    assert not root.exists()


def test_the_fake_records_the_shas_of_commits_missing_from():
    io = FakeGit()
    try:
        sha = io.head_commit(io.repo)
        io.commits_missing_from(io.repo, [sha, "main"], "main")
        verb, args = io.calls[-1]
        assert verb == "commits_missing_from"
        assert args == (io.repo, (sha, "main"), "main")
    finally:
        io.temp.remove()


