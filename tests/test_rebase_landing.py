"""A rebase-merged request records its exact landing range (B178).

A forge's "rebase and merge" lands N commits; `merge_sha^1` is then the second-to-last of
them, not the old target, so a cherry-pick port of it carried only the last commit. ddflow
now reads the base tip before it merges, checks the range against the request's own
commit count, and falls back to comparing the change itself when it cannot.
"""

# ruff: noqa: F811  (fixtures imported from test_flow)
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from conftest import pass_pipeline, run_cli
from test_flow import AUTHOR, _commit, _git, _state, pr_repo  # noqa: F401

GREEN = [{"status": "COMPLETED", "conclusion": "SUCCESS"}]


def _three_commit_fix(repo: Path) -> None:
    run_cli(repo, "task", "add", "T1", "--globs", "a.py,b.py,c.py")
    code, out, err = run_cli(repo, "--json", "claim", "T1")
    assert code == 0, err
    tree = Path(json.loads(out)["worktree"])
    for name in ("a.py", "b.py", "c.py"):
        _commit(tree, name, f"{name}\n", f"feat: {name}")
    pass_pipeline(repo, "T1", omit=("merge",))
    code, _o, err = run_cli(repo, "merge", "T1", "--model", AUTHOR)
    assert code == 0, err


def _rebase_project(pr_repo):
    repo, forge, remote = pr_repo
    run_cli(repo, "config", "--set", "worktree.merge_strategy", "ff-only")
    _three_commit_fix(repo)
    forge.approve(1)
    forge.edit(1, checks=GREEN)
    return repo, forge, remote


def _range_files(repo: Path, before: str, after: str) -> list[str]:
    return sorted(_git(repo, "diff", "--name-only", before, after).split())


def test_a_rebase_merge_by_ddflow_records_the_whole_range(pr_repo):
    repo, forge, remote = _rebase_project(pr_repo)
    old_tip = subprocess.run(
        ["git", "--git-dir", str(remote), "rev-parse", "main"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    code, _out, err = run_cli(repo, "--json", "pr", "sync")
    assert code == 0, err
    assert forge.calls("pr", "merge")[0].count("--rebase") == 1, "not a rebase merge"
    it = _state(repo).items["T1"]
    assert it.landed_before == old_tip, (it.landed_before, old_tip)
    assert it.landed_after == forge.pr(1)["merge_sha"]
    assert _range_files(repo, it.landed_before, it.landed_after) == ["a.py", "b.py", "c.py"]


def test_a_foreign_commit_landing_during_the_merge_does_not_widen_the_range(pr_repo):
    repo, forge, _remote = _rebase_project(pr_repo)
    forge.set(foreign_on_merge=True)
    run_cli(repo, "pr", "sync")
    it = _state(repo).items["T1"]
    files = _range_files(repo, it.landed_before, it.landed_after)
    assert files == ["a.py", "b.py", "c.py"], f"foreign.txt or a partial range recorded: {files}"


def test_a_squash_merge_is_still_exact_and_unchanged(pr_repo):
    repo, forge, _remote = pr_repo
    run_cli(repo, "config", "--set", "worktree.merge_strategy", "squash")
    _three_commit_fix(repo)
    forge.approve(1)
    forge.edit(1, checks=GREEN)
    run_cli(repo, "pr", "sync")
    it = _state(repo).items["T1"]
    assert _range_files(repo, it.landed_before, it.landed_after) == ["a.py", "b.py", "c.py"]
    assert it.landed_before == _git(repo, "rev-parse", f"{it.landed_after}^1")


def test_a_rebase_merge_by_a_person_is_recognised_from_the_change_itself(pr_repo):
    repo, forge, _remote = pr_repo
    _three_commit_fix(repo)
    forge.merge_as_human(1, "--rebase")
    run_cli(repo, "pr", "sync")
    it = _state(repo).items["T1"]
    assert _range_files(repo, it.landed_before, it.landed_after) == ["a.py", "b.py", "c.py"]


def test_a_squash_whose_range_matches_the_commit_count_by_coincidence_is_still_a_squash(pr_repo):
    """Two commits, squash strategy, one foreign commit lands first: base_before..merge is
    2 commits -- the request's count -- but it is NOT a rebase, and merge_sha^1 is exact."""
    repo, forge, _remote = pr_repo
    run_cli(repo, "config", "--set", "worktree.merge_strategy", "squash")
    run_cli(repo, "task", "add", "T1", "--globs", "a.py,b.py")
    code, out, err = run_cli(repo, "--json", "claim", "T1")
    assert code == 0, err
    tree = Path(json.loads(out)["worktree"])
    for name in ("a.py", "b.py"):
        _commit(tree, name, f"{name}\n", f"feat: {name}")
    pass_pipeline(repo, "T1", omit=("merge",))
    assert run_cli(repo, "merge", "T1", "--model", AUTHOR)[0] == 0
    forge.approve(1)
    forge.edit(1, checks=GREEN)
    forge.set(foreign_on_merge=True)
    run_cli(repo, "pr", "sync")
    it = _state(repo).items["T1"]
    assert it.landed_before == _git(repo, "rev-parse", f"{it.landed_after}^1"), (
        "a squash's range must start at its first parent, not at the pre-merge tip"
    )
    assert _range_files(repo, it.landed_before, it.landed_after) == ["a.py", "b.py"]


def test_the_commit_count_reads_gh_arrays_and_graphql_connections():
    from ddflow.infra.forge import _count

    assert _count([{"oid": "a"}, {"oid": "b"}, {"oid": "c"}]) == 3  # `gh pr view --json commits`
    assert _count({"totalCount": 5, "nodes": [{}]}) == 5  # a connection-shaped answer
    assert _count({"nodes": [{}, {}]}) == 2
    assert _count(None) == 0


def test_the_pre_merge_tip_proves_a_range_the_patch_id_cannot(pr_repo, tmp_path):
    """The base moved (an edit two lines from the request's hunk) before the rebase: the
    replayed commits' context differs, so no patch-id matches the request's own diff --
    only the tip ddflow read before merging, plus the commit count, proves the range."""
    repo, forge, remote = pr_repo
    run_cli(repo, "config", "--set", "worktree.merge_strategy", "ff-only")
    (repo / "lib.txt").write_text("".join(f"l{i}\n" for i in range(1, 10)))
    _git(repo, "add", "lib.txt")
    _git(repo, "commit", "-qm", "lib")
    _git(repo, "push", "-q", "origin", "main")

    run_cli(repo, "task", "add", "T1", "--globs", "lib.txt,x.py")
    code, out, err = run_cli(repo, "--json", "claim", "T1")
    assert code == 0, err
    tree = Path(json.loads(out)["worktree"])
    text = (tree / "lib.txt").read_text()
    (tree / "lib.txt").write_text(text.replace("l5\n", "pr5\n"))
    _git(tree, "commit", "-qam", "feat: pr5")
    (tree / "lib.txt").write_text((tree / "lib.txt").read_text().replace("l9\n", "pr9\n"))
    _git(tree, "commit", "-qam", "feat: pr9")
    _commit(tree, "x.py", "x\n", "feat: x")
    pass_pipeline(repo, "T1", omit=("merge",))
    assert run_cli(repo, "merge", "T1", "--model", AUTHOR)[0] == 0

    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(remote), str(other)], check=True)
    for k, v in (("user.email", "o@example.com"), ("user.name", "O")):
        _git(other, "config", k, v)
    (other / "lib.txt").write_text((other / "lib.txt").read_text().replace("l7\n", "foreign7\n"))
    _git(other, "commit", "-qam", "foreign: l7")
    _git(other, "push", "-q", "origin", "main")
    moved = _git(other, "rev-parse", "HEAD")

    forge.approve(1)
    forge.edit(1, checks=GREEN)
    run_cli(repo, "pr", "sync")
    it = _state(repo).items["T1"]
    assert it.landed_before == moved, (it.landed_before, moved)
    assert _git(repo, "rev-list", "--count", f"{it.landed_before}..{it.landed_after}") == "3"
    assert _range_files(repo, it.landed_before, it.landed_after) == ["lib.txt", "x.py"]


def test_the_remote_tip_needs_no_remote_tracking_ref(pr_repo):
    from ddflow.config import Config
    from ddflow.services.flow import _remote_tip

    repo, _forge, remote = pr_repo
    # A remote with no fetch refspec: a fetch writes FETCH_HEAD and no remote-tracking ref.
    _git(repo, "config", "--unset-all", "remote.origin.fetch")
    subprocess.run(
        ["git", "-C", str(repo), "update-ref", "-d", "refs/remotes/origin/main"], check=False
    )
    want = subprocess.run(
        ["git", "--git-dir", str(remote), "rev-parse", "main"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert _remote_tip(repo, Config.load(repo), "main") == want
    assert _remote_tip(repo, Config.load(repo), "no-such-branch") == ""
