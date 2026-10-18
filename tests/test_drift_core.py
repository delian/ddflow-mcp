"""services/drift.py: the one drift calculation (D-drift-measure, D-item-base-branch)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from ddflow.core.records import Decision
from ddflow.infra.log import EventLog
from ddflow.services import drift as D


def _git(cwd: Path, *a: str) -> str:
    r = subprocess.run(
        ["git", "-C", str(cwd), "-c", "user.name=t", "-c", "user.email=t@t", *a],
        capture_output=True,
        text=True,
        check=True,
    )
    return r.stdout.strip()


def _commit(cwd: Path, name: str, text: str = "x\n", msg: str = "") -> str:
    p = cwd / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    _git(cwd, "add", "-A")
    _git(cwd, "commit", "-q", "-m", msg or f"touch {name}")
    return _git(cwd, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "r"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    (r / ".git" / "info" / "exclude").write_text(".ddflow/\n")
    _commit(r, "README.md", "hi\n", "root")
    return r


def _tree(repo: Path, name: str, base: str, item: str) -> Path:
    wt = repo.parent / name
    _git(repo, "worktree", "add", "-q", "-b", name, str(wt), base)
    EventLog(repo, "agent-test").append(
        "worktree.created", item, {"path": str(wt), "branch": name, "base": base}
    )
    return wt


def test_gitflow_main_far_ahead_of_develop_is_not_drift_for_a_develop_item(repo):
    """The item's recorded base is `develop`; `main` racing ahead is not its drift."""
    _git(repo, "branch", "develop")
    wt = _tree(repo, "feat", "develop", "T1")
    for i in range(5):
        _commit(repo, f"m{i}.txt")
    assert D.base_of(repo, wt) == "develop"
    r = D.measure(repo, wt)
    assert (r.base, r.behind) == ("develop", 0)
    _git(repo, "checkout", "-q", "develop")
    _commit(repo, "d.txt", "d\n", "develop moves")
    r = D.measure(repo, wt)
    assert r.behind == 1 and r.commits[0].subject == "develop moves"


def test_a_stacked_base_that_was_deleted_falls_back_to_the_default_branch(repo):
    wt = _tree(repo, "feat", "main", "T2")
    EventLog(repo, "agent-test").append(
        "worktree.created", "T2", {"path": str(wt), "branch": "feat", "base": "gone"}
    )
    assert D.base_of(repo, wt) == "main"


def test_relevant_and_unrelated_commits_are_told_apart(repo):
    wt = _tree(repo, "feat", "main", "T3")
    _commit(repo, "src/a.py")
    _commit(repo, "docs/b.md")
    r = D.measure(repo, wt, globs=["src/"], rulebooks=())
    assert r.behind == 2
    assert [c.paths for c in r.relevant] == [("src/a.py",)]
    assert [c.paths for c in r.unrelated] == [("docs/b.md",)]
    assert D.measure(repo, wt).relevant == ()


def test_since_limits_the_commits_to_those_after_the_last_seen_sha(repo):
    wt = _tree(repo, "feat", "main", "T4")
    first = _commit(repo, "a.txt")
    _commit(repo, "b.txt")
    r = D.measure(repo, wt, since=first)
    assert r.behind == 2 and [c.paths for c in r.commits] == [("b.txt",)]


def test_unrelated_history_is_could_not_tell_never_zero(repo):
    wt = _tree(repo, "feat", "main", "T5")
    assert D.measure(repo, wt, "no-such-branch").behind is None


def test_renamed_and_deleted_paths_are_named(repo):
    _commit(repo, "old.py", "def f():\n    return 1\n" * 5)
    _commit(repo, "gone.py", "g\n" * 5)
    since = _git(repo, "rev-parse", "HEAD")
    _git(repo, "mv", "old.py", "new.py")
    _git(repo, "rm", "-q", "gone.py")
    _git(repo, "commit", "-q", "-m", "mv")
    got = D.moved_or_gone(repo, ["old.py", "gone.py", "README.md"], since)
    assert got == {"old.py": "new.py", "gone.py": None}


def test_decisions_added_or_superseded_after_a_time_that_govern_the_globs():
    ds = {
        "old": Decision("old", globs=["src/"], at="2026-01-01", superseded_by="new"),
        "new": Decision("new", globs=["src/"], at="2026-03-01"),
        "other": Decision("other", globs=["docs/"], at="2026-03-01"),
    }
    got = D.decisions_changed(SimpleNamespace(decisions=ds), ["src/a.py"], "2026-02-01")
    assert [(c.id, c.how) for c in got] == [("new", "added"), ("old", "superseded")]
    assert D.decisions_changed(SimpleNamespace(decisions=ds), ["src/a.py"], "2026-04-01") == []


def test_stamp_coverage_and_last_seen(repo):
    a = _commit(repo, "a.txt")
    b = _commit(repo, "b.txt")
    assert D.stamp(repo, "main") == b and D.stamp(repo, "nope") == ""
    assert D.is_covered(repo, [a], b) and D.uncovered(repo, [a, b], a) == [b]
    assert D.last_seen(repo, repo) == ""
    D.set_last_seen(repo, repo, b)
    assert D.last_seen(repo, repo) == b
