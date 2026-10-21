"""B-stale-rules-escape-needs-file-copy: `ddflow sync --keep-local` merges the base into a
DIRTY worktree without losing the uncommitted work (a stash-safe merge), and the
stale_rules refusal names that one command."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_sync_command import ME, _claimed, _git, _land

from ddflow.api.lifecycle.sync import sync


def _stashes(cwd) -> str:
    return _git(cwd, "stash", "list")


def test_keep_local_merges_and_restores_uncommitted_and_untracked_work(repo):
    wt = _claimed(repo)
    _land(repo, "src/a.py")
    (wt / "README.md").write_text("dirty tracked\n")
    (wt / "new.txt").write_text("untracked\n")
    out = sync(repo, item="T1", keep_local=True, agent=ME)
    assert out.ok, out.reason
    assert out.data["status"] == "merged" and (wt / "src" / "a.py").exists()
    assert (wt / "README.md").read_text() == "dirty tracked\n"
    assert (wt / "new.txt").read_text() == "untracked\n"
    assert "ddflow-sync-keep-local" not in _stashes(wt)  # our entry is dropped


def test_without_keep_local_a_dirty_tree_is_still_refused(repo):
    wt = _claimed(repo)
    _land(repo, "src/a.py")
    (wt / "README.md").write_text("dirty\n")
    out = sync(repo, item="T1", agent=ME)
    assert out.data["status"] == "refused"
    assert "--keep-local" in out.reason  # the refusal names the supported way out


def test_keep_local_conflicting_with_the_base_restores_the_tree(repo):
    wt = _claimed(repo)
    _land(repo, "src/c.py", "main\n")
    (wt / "src").mkdir(exist_ok=True)
    (wt / "src" / "c.py").write_text("branch\n")
    _git(wt, "add", "src/c.py")
    _git(wt, "commit", "-qm", "branch c")
    (wt / "README.md").write_text("dirty\n")
    head = _git(wt, "rev-parse", "HEAD")
    out = sync(repo, item="T1", keep_local=True, agent=ME)
    assert out.data["status"] == "conflict"
    assert _git(wt, "rev-parse", "HEAD") == head
    assert (wt / "README.md").read_text() == "dirty\n"
    assert "ddflow-sync-keep-local" not in _stashes(wt)


def test_a_stash_pushed_meanwhile_by_another_worktree_is_not_dropped(repo):
    from ddflow.services import branchsync as BS

    wt = _claimed(repo)
    (wt / "a.txt").write_text("mine\n")
    mine = BS._stash_away(wt, "tag-mine")
    _sha, ref = BS._stash_find(wt, "tag-mine")  # the selector, as _stash_back held it
    (wt / "b.txt").write_text("theirs\n")
    _git(wt, "stash", "push", "-u", "-m", "tag-theirs")  # another session, in between
    BS._drop(wt, ref, mine)
    left = _stashes(wt)
    assert "tag-theirs" in left and "tag-mine" not in left, left


def test_stash_back_survives_a_stash_pushed_between_its_lookup_and_its_drop(repo, monkeypatch):
    """The race through `_stash_back` itself: another worktree pushes right after the lookup."""
    from ddflow.services import branchsync as BS

    wt = _claimed(repo)
    (wt / "a.txt").write_text("mine\n")
    mine = BS._stash_away(wt, "tag-mine")
    real = BS._stash_find
    pushed = []

    def find_then_a_stranger_pushes(tree, tag):
        found = real(tree, tag)
        if tag == "tag-mine" and not pushed:
            pushed.append(1)
            (tree / "b.txt").write_text("theirs\n")
            _git(tree, "stash", "push", "-u", "-m", "tag-theirs", "--", "b.txt")
        return found

    monkeypatch.setattr(BS, "_stash_find", find_then_a_stranger_pushes)
    assert BS._stash_back(wt, "tag-mine", mine) == ""
    left = _stashes(wt)
    assert "tag-theirs" in left and "tag-mine" not in left, left
    assert (wt / "a.txt").read_text() == "mine\n"


def test_every_find_during_the_drop_may_race_with_a_new_stranger(repo, monkeypatch):
    """Two strangers push, one after each lookup: the verified, bounded drop loses neither."""
    from ddflow.services import branchsync as BS

    wt = _claimed(repo)
    (wt / "a.txt").write_text("mine\n")
    mine = BS._stash_away(wt, "tag-mine")
    real = BS._stash_find
    n = []

    def racing(tree, tag):
        found = real(tree, tag)
        if len(n) < 2:
            n.append(1)
            (tree / f"s{len(n)}.txt").write_text("theirs\n")
            _git(tree, "stash", "push", "-u", "-m", f"tag-theirs-{len(n)}", "--", f"s{len(n)}.txt")
        return found

    monkeypatch.setattr(BS, "_stash_find", racing)
    assert BS._stash_back(wt, "tag-mine", mine) == ""
    left = _stashes(wt)
    assert "tag-theirs-1" in left and "tag-theirs-2" in left and "tag-mine" not in left, left


def test_keep_local_work_that_conflicts_with_the_base_stays_in_a_kept_stash(repo):
    wt = _claimed(repo)
    _land(repo, "README.md", "base\n")
    (wt / "README.md").write_text("local\n")
    out = sync(repo, item="T1", keep_local=True, agent=ME)
    assert out.data["status"] == "merged" and out.data["kept_stash"]
    assert out.data["kept_stash"][:12] in out.reason or "kept in stash" in str(out.data)
    assert "ddflow-sync-keep-local" in _stashes(wt)  # the work is not lost
