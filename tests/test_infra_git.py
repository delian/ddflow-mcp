"""infra.git.run: the one git runner (B-uni-proc.1-git)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ddflow.core.outcome import FAIL, declared_exit
from ddflow.infra import git as G
from ddflow.infra import proc as P
from ddflow.infra import worktree as W


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "r"
    repo.mkdir()
    for args in (["init", "-q"], ["config", "user.email", "a@b"], ["config", "user.name", "n"]):
        subprocess.run(["git", "-C", str(repo), *args], check=True, stdin=subprocess.DEVNULL)
    return repo


def test_old_names_are_the_same_objects():
    assert W.git is G.run
    assert W.git_paths is G.git_paths
    assert W.GitError is G.GitError
    assert W.GitResult is G.GitResult


def test_text_result_is_stripped(tmp_path):
    repo = _repo(tmp_path)
    r = G.run(repo, "rev-parse", "--is-inside-work-tree")
    assert r.ok and r.out == "true" and not r.unavailable


def test_failure_is_a_result_not_an_exception(tmp_path):
    r = G.run(tmp_path, "rev-parse", "--verify", "nope")
    assert not r.ok and not r.unavailable and r.code != 0


def test_missing_git_is_unavailable(monkeypatch, tmp_path):
    def boom(*a, **k):
        raise FileNotFoundError("git")

    monkeypatch.setattr(P, "run", boom)
    r = G.run(tmp_path, "status")
    assert r.unavailable and not r.ok and "could not run" in r.err


def test_timeout_is_unavailable_and_timed_out(monkeypatch, tmp_path):
    def slow(*a, **k):
        raise P.TimeoutExpired("git", 1)

    monkeypatch.setattr(P, "run", slow)
    r = G.run(tmp_path, "status", timeout=1)
    assert r.unavailable and r.timed_out and not r.ok
    assert G.git_paths(tmp_path, "ls-files") is None


def test_check_raises_git_error_with_a_declared_exit(tmp_path):
    with pytest.raises(G.GitError) as e:
        G.run(tmp_path, "rev-parse", "--verify", "nope", check=True)
    assert declared_exit(e.value) == FAIL


def test_locale_is_c(monkeypatch, tmp_path):
    seen = {}

    def spy(argv, **kw):
        seen.update(kw["env"])
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    monkeypatch.setattr(P, "run", spy)
    G.run(tmp_path, "status", env={"X": "1"})
    assert seen["LC_ALL"] == "C" and seen["X"] == "1"


def test_binary_keeps_trailing_newline_and_feeds_input(tmp_path):
    repo = _repo(tmp_path)
    (repo / "f").write_text("a\n")
    G.run(repo, "add", "f", check=True)
    G.run(repo, "commit", "-qm", "m", check=True)
    shown = G.run(repo, "show", "HEAD:f", binary=True)
    assert shown.out_bytes == b"a\n" and shown.out == "a"
    diff = G.run(repo, "show", "--format=", "HEAD", binary=True)
    ids = G.run(repo, "patch-id", "--stable", input=diff.out_bytes)
    assert ids.ok and len(ids.out.split()) == 2


def test_z_listing_round_trips_odd_names(tmp_path):
    repo = _repo(tmp_path)
    names = ["café.txt", "a b.txt"]
    for n in names:
        (repo / n).write_text("x")
    bad = repo.as_posix().encode() + b"/bad\xff.txt"
    Path(bad.decode("utf-8", "surrogateescape")).write_text("x")
    got = G.git_paths(repo, "ls-files", "--others", "--", ".")
    assert got is not None
    assert set(got) >= set(names) and any("\udcff" in g for g in got)
    # pathspec after "--" keeps working: -z goes before it
    assert G.git_paths(repo, "ls-files", "--others", "--", "a b.txt") == ["a b.txt"]
    assert G.git_paths(repo, "bogus-subcommand") is None
