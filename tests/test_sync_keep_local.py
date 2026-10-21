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
    assert "git stash show -p" in out.data["text"] and "git stash apply" not in out.data["text"]
    assert "ddflow-sync-keep-local" in _stashes(wt)  # the work is not lost


def test_keep_local_keeps_staged_work_staged(repo):
    wt = _claimed(repo)
    _land(repo, "src/a.py")
    (wt / "README.md").write_text("staged\n")
    _git(wt, "add", "README.md")
    out = sync(repo, item="T1", keep_local=True, agent=ME)
    assert out.ok, out.reason
    assert _git(wt, "diff", "--cached", "--name-only") == "README.md"


# --- B-sync-keep-local-stash-edges: git failures and races must be reported, never swallowed


def _fail():
    from types import SimpleNamespace

    return SimpleNamespace(ok=False, out="", err="simulated git failure", code=1)


def test_a_failed_stash_list_after_the_push_is_not_taken_for_no_entry(repo, monkeypatch):
    """(c) The push worked, `stash list` fails every time: refuse and name the tag."""
    from ddflow.services import branchsync as BS

    wt = _claimed(repo)
    (wt / "a.txt").write_text("mine\n")
    real = BS.W.git

    def git(tree, *a, **kw):
        if a[:2] == ("stash", "list") or a[:2] == ("rev-parse", "--verify"):
            return _fail()
        return real(tree, *a, **kw)

    monkeypatch.setattr(BS.W, "git", git)
    try:
        BS._stash_away(wt, "tag-mine")
    except RuntimeError as e:
        assert "tag-mine" in str(e) and "Nothing was merged" in str(e)
    else:
        raise AssertionError("an unlocatable stash must refuse, not return")
    assert "tag-mine" in _stashes(wt)  # the work is there, and the message says so


def test_a_transient_stash_list_failure_after_the_push_is_retried(repo, monkeypatch):
    from ddflow.services import branchsync as BS

    wt = _claimed(repo)
    (wt / "a.txt").write_text("mine\n")
    real = BS.W.git
    seen = []

    def git(tree, *a, **kw):
        if a[:2] == ("stash", "list") and not seen:
            seen.append(1)
            return _fail()
        return real(tree, *a, **kw)

    monkeypatch.setattr(BS.W, "git", git)
    assert BS._stash_away(wt, "tag-mine")  # found on the retry, not "" (= no entry)


def test_keep_local_with_an_unlocatable_stash_refuses_through_sync(repo, monkeypatch):
    from ddflow.services import branchsync as BS

    wt = _claimed(repo)
    _land(repo, "src/a.py")
    (wt / "README.md").write_text("dirty\n")
    real = BS.W.git
    head = _git(wt, "rev-parse", "HEAD")

    def git(tree, *a, **kw):
        if a[:2] in (("stash", "list"), ("rev-parse", "--verify")):
            return _fail()
        return real(tree, *a, **kw)

    monkeypatch.setattr(BS.W, "git", git)
    out = sync(repo, item="T1", keep_local=True, agent=ME)
    assert out.data["status"] == "refused" and "ddflow-sync-keep-local" in out.reason
    assert _git(wt, "rev-parse", "HEAD") == head  # nothing merged


def test_a_stranger_entry_that_cannot_be_stored_back_is_reported(repo, monkeypatch):
    """(a) The drop took a stranger's entry and `stash store` fails: say so, with the sha."""
    from ddflow.services import branchsync as BS

    wt = _claimed(repo)
    (wt / "a.txt").write_text("mine\n")
    mine = BS._stash_away(wt, "tag-mine")
    _sha, ref = BS._stash_find(wt, "tag-mine")
    (wt / "b.txt").write_text("theirs\n")
    _git(wt, "stash", "push", "-u", "-m", "tag-theirs")
    theirs = _git(wt, "rev-parse", "stash@{0}")
    real = BS.W.git

    def git(tree, *a, **kw):
        if a[:2] == ("stash", "store"):
            return _fail()
        return real(tree, *a, **kw)

    monkeypatch.setattr(BS.W, "git", git)
    why = BS._drop(wt, ref, mine)
    assert why.startswith(BS.DROP_INCOMPLETE)
    assert theirs[:12] in why and "stash store" in why
    assert theirs not in _git(wt, "stash", "list", "--format=%H")  # truly dropped, and said so


def test_giving_up_after_five_races_is_not_reported_as_done(repo, monkeypatch):
    """(b) A stranger pushes before every drop: the loop gives up, and says our entry remains."""
    from ddflow.services import branchsync as BS

    wt = _claimed(repo)
    (wt / "a.txt").write_text("mine\n")
    mine = BS._stash_away(wt, "tag-mine")
    _sha, ref = BS._stash_find(wt, "tag-mine")
    real = BS.W.git
    n = []

    def git(tree, *a, **kw):
        if a[:2] == ("stash", "drop"):
            n.append(1)
            (tree / f"s{len(n)}.txt").write_text("theirs\n")
            real(tree, "stash", "push", "-u", "-m", f"tag-theirs-{len(n)}", "--", f"s{len(n)}.txt")
        return real(tree, *a, **kw)

    monkeypatch.setattr(BS.W, "git", git)
    why = BS._drop(wt, ref, mine)
    assert why.startswith(BS.DROP_INCOMPLETE) and mine[:12] in why, why
    assert "tag-mine" in _stashes(wt)  # the leftover is real and was named


def test_sync_exits_non_zero_when_the_stash_could_not_be_cleaned_up(repo, monkeypatch):
    from ddflow.services import branchsync as BS

    wt = _claimed(repo)
    _land(repo, "src/a.py")
    (wt / "README.md").write_text("dirty\n")
    real = BS.W.git
    out_ref = {}

    def git(tree, *a, **kw):
        if a[:2] == ("stash", "drop"):
            return _fail()
        if a[:2] == ("stash", "list") and out_ref.get("after_apply"):
            return _fail()
        if a[:2] == ("stash", "apply"):
            out_ref["after_apply"] = True
        return real(tree, *a, **kw)

    monkeypatch.setattr(BS.W, "git", git)
    out = sync(repo, item="T1", keep_local=True, agent=ME)
    assert not out.ok and "STASH NOT CLEANED UP" in out.reason
    assert (wt / "README.md").read_text() == "dirty\n"


def test_a_clean_tree_needs_no_stash_list_even_when_the_list_fails(repo, monkeypatch):
    """Nothing to stash is not an unlocatable entry (rubber_duck round 1)."""
    from ddflow.services import branchsync as BS

    wt = _claimed(repo)
    real = BS.W.git

    def git(tree, *a, **kw):
        if a[:2] in (("stash", "list"), ("rev-parse", "--verify")):
            return _fail()
        return real(tree, *a, **kw)

    monkeypatch.setattr(BS.W, "git", git)
    assert BS._stash_away(wt, "tag-mine") == ""


def test_a_tag_that_reads_like_the_nothing_to_save_message_is_still_stashed(repo):
    from ddflow.services import branchsync as BS

    wt = _claimed(repo)
    (wt / "a.txt").write_text("mine\n")
    assert BS._stash_away(wt, "No local changes to save")  # a stash WAS made: not ""
    assert "No local changes to save" in _stashes(wt)


def test_a_pushed_stash_missing_from_a_successful_list_is_not_taken_for_none(repo, monkeypatch):
    """critic round 1: the list worked but the entry we just pushed is not in it (another
    session has it out of the stack for an instant): unknown, not "there was no work"."""
    from ddflow.services import branchsync as BS

    wt = _claimed(repo)
    (wt / "a.txt").write_text("mine\n")
    real = BS._stash_find
    calls = []

    def find(tree, tag):
        calls.append(1)
        return ("", "") if len(calls) == 1 else real(tree, tag)

    monkeypatch.setattr(BS, "_stash_find", find)
    assert BS._stash_away(wt, "tag-mine")  # found on the retry


def test_a_failing_list_is_recovered_through_the_newest_entry_when_it_is_ours(repo, monkeypatch):
    """roborev: the refs/stash fallback of `_stash_away` returns our entry, by its tag."""
    from ddflow.services import branchsync as BS

    wt = _claimed(repo)
    (wt / "a.txt").write_text("mine\n")
    real = BS.W.git

    def git(tree, *a, **kw):
        if a[:2] == ("stash", "list"):
            return _fail()
        return real(tree, *a, **kw)

    monkeypatch.setattr(BS.W, "git", git)
    sha = BS._stash_away(wt, "tag-mine")
    assert sha == _git(wt, "rev-parse", "stash@{0}")


def test_a_tag_the_list_cannot_show_is_refused_not_taken_for_no_work(repo):
    """rubber_duck round 3: a multi-line tag is cut by `%gs`; the pushed stash must not be
    reported as "nothing stashed" (the entry is located by tag or the sync refuses)."""
    from ddflow.services import branchsync as BS

    wt = _claimed(repo)
    (wt / "a.txt").write_text("mine\n")
    try:
        sha = BS._stash_away(wt, "line1\nline2")
    except RuntimeError as e:
        assert "Nothing was merged" in str(e)
    else:
        assert sha, "a stash that exists was reported as no stash"


def test_an_entry_missing_from_one_good_list_after_the_apply_is_looked_for_again(repo, monkeypatch):
    """critic round 2: our entry exists after `stash apply`; an empty answer is not "gone"."""
    from ddflow.services import branchsync as BS

    wt = _claimed(repo)
    (wt / "a.txt").write_text("mine\n")
    mine = BS._stash_away(wt, "tag-mine")
    real = BS._stash_find
    calls = []

    def find(tree, tag):
        calls.append(1)
        return ("", "") if len(calls) == 1 else real(tree, tag)

    monkeypatch.setattr(BS, "_stash_find", find)
    assert BS._stash_back(wt, "tag-mine", mine) == ""
    assert "tag-mine" not in _stashes(wt)  # dropped, not reported done while still there


def test_a_drop_that_does_not_say_what_it_dropped_is_not_a_clean_drop(repo, monkeypatch):
    """roborev: an unparseable answer is "could not tell", never success."""
    from ddflow.services import branchsync as BS

    wt = _claimed(repo)
    (wt / "a.txt").write_text("mine\n")
    mine = BS._stash_away(wt, "tag-mine")
    _sha, ref = BS._stash_find(wt, "tag-mine")
    real = BS.W.git

    def git(tree, *a, **kw):
        r = real(tree, *a, **kw)
        if a[:2] == ("stash", "drop"):
            return BS.W.GitResult(r.code, "Dropped (no sha shown)", r.err)
        return r

    monkeypatch.setattr(BS.W, "git", git)
    assert BS._drop(wt, ref, mine).startswith(BS.DROP_INCOMPLETE)
