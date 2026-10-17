"""`ci run`'s scratch merge runs no repository hook and never calls a timeout a conflict
(B36c484af97).

The post-merge hook (`pins regenerate --after-merge`) ran the golden suite inside the scratch
`git merge`, which took over 300 s under load; the merge was killed and the gate recorded
FAILED "does not merge" for a branch that merged cleanly. Two defects: the scratch merge must
not run hooks at all, and a merge git could not finish is UNAVAILABLE (the gate could not
say), never a conflict.
"""

from __future__ import annotations

import os
import stat

import pytest
from conftest import run_cli
from helpers import commit as _commit
from helpers import git as _git

from ddflow.config import Config
from ddflow.infra import git as IG
from ddflow.services import ci as CI


@pytest.fixture
def passing_precommit(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    pc = bin_dir / "pre-commit"
    pc.write_text("#!/bin/sh\necho 'ruff check.....................Passed'\n")
    pc.chmod(pc.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")


def _diverged(repo):
    """A branch and a main that each moved on, so the scratch merge is a real merge."""
    run_cli(repo, "init")
    (repo / ".pre-commit-config.yaml").write_text("repos: []\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "adopt")
    default = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    _git(repo, "switch", "-q", "-c", "work")
    _commit(repo, {"b.txt": "b\n"}, "work")
    _git(repo, "switch", "-q", default)
    _commit(repo, {"a.txt": "a\n"}, "main moved on")
    _git(repo, "switch", "-q", "work")
    return default


def test_the_scratch_merge_runs_no_post_merge_hook(repo, passing_precommit, tmp_path):
    default = _diverged(repo)
    marker = tmp_path / "hook-ran"
    hook = repo / ".git" / "hooks" / "post-merge"
    hook.write_text(f"#!/bin/sh\ntouch {marker}\n")
    hook.chmod(0o755)
    res = CI.run(repo, Config.load(repo), base=default)
    assert res.ok, res
    assert not marker.exists(), "the repository's post-merge hook ran inside the scratch merge"


def test_a_scratch_merge_git_could_not_finish_is_unavailable_not_a_conflict(
    repo, passing_precommit, monkeypatch
):
    default = _diverged(repo)
    real = CI.W.git

    def killed(path, *args, **kw):
        if "merge" in args and "--no-ff" in args:
            return IG.GitResult(
                IG.UNAVAILABLE,
                "",
                "git merge timed out after 300s",
                unavailable=True,
                timed_out=True,
            )
        return real(path, *args, **kw)

    monkeypatch.setattr(CI.W, "git", killed)
    res = CI.run(repo, Config.load(repo), base=default)
    assert res.status == "unavailable" and res.exit == 2, res
    assert "timed out" in res.reason and "does not merge" not in res.reason


def test_a_merge_git_refused_for_another_reason_is_not_a_conflict_either(
    repo, passing_precommit, monkeypatch
):
    """A base git cannot resolve, a held index lock: exit 128, not a conflict (exit 1)."""
    default = _diverged(repo)
    real = CI.W.git

    def refused(path, *args, **kw):
        if "merge" in args and "--no-ff" in args:
            return IG.GitResult(128, "", "fatal: 'main' - not something we can merge")
        return real(path, *args, **kw)

    monkeypatch.setattr(CI.W, "git", refused)
    res = CI.run(repo, Config.load(repo), base=default)
    assert res.status == "unavailable", res
    assert "not something we can merge" in res.reason


def test_merge_tree_yields_a_structured_kind_not_prose(repo, passing_precommit):
    """Bug Baf8f6a7c74: run() told a conflict from a merge it could not do by its wording."""
    default = _diverged(repo)
    sha = CI.W.rev(repo, "HEAD")
    with CI.merge_tree(repo, sha, default) as (tree, why):
        assert tree is not None and why.kind == "ok"
    with CI.merge_tree(repo, sha, "no-such-base") as (tree, why):
        assert tree is None and why.kind == "cannot-merge"
    _git(repo, "switch", "-q", default)
    _commit(repo, {"b.txt": "other\n"}, "main edits b")
    _git(repo, "switch", "-q", "work")
    with CI.merge_tree(repo, sha, default) as (tree, why):
        assert tree is None and why.kind == "conflict"


def test_a_reworded_conflict_message_is_still_failed(repo, passing_precommit, monkeypatch):
    default = _diverged(repo)
    _git(repo, "switch", "-q", default)
    _commit(repo, {"b.txt": "other\n"}, "main edits b")
    _git(repo, "switch", "-q", "work")
    monkeypatch.setattr(CI, "NOT_MERGEABLE", "clashes with")
    res = CI.run(repo, Config.load(repo), base=default)
    assert res.status == "failed" and "clashes with" in res.reason, res


def test_a_failure_survives_copy_and_pickle():
    import copy
    import pickle

    f = CI.Failure("boom", CI.CONFLICT)
    for g in (copy.deepcopy(f), pickle.loads(pickle.dumps(f))):
        assert g == "boom" and g.kind == CI.CONFLICT
