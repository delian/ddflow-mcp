"""Leases, and the crash-recovery behaviour that is the reason they are events."""

import multiprocessing as mp
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from orchard import lease as L
from orchard import worktree as W
from orchard.config import Config
from orchard.events import EventLog
from orchard.model import fold


def seed(log, ids=("T1", "T2", "T3")):
    log.append("phase.added", "P1", {})
    for _i, t in enumerate(ids):
        log.append("task.added", t, {"parent": "P1", "globs": [f"src/{t}/*"]})


def test_acquire_then_conflicting_acquire_is_refused(log, cfg):
    seed(log)
    L.acquire(log, cfg, "T1", holder="a")
    with pytest.raises(L.LeaseError) as exc:
        L.acquire(log, cfg, "T1", holder="b")
    assert exc.value.holder == "a"


def test_refusal_offers_alternatives(log, cfg):
    seed(log)
    L.acquire(log, cfg, "T1", holder="a")
    with pytest.raises(L.LeaseError) as exc:
        L.acquire(log, cfg, "T1", holder="b")
    assert "T2" in exc.value.alternatives, "a refusal must name what to do instead"


def test_overlapping_globs_are_refused_even_on_a_free_item(log, cfg):
    log.append("phase.added", "P1", {})
    log.append("task.added", "A", {"parent": "P1", "globs": ["src/shared/*"]})
    log.append("task.added", "B", {"parent": "P1", "globs": ["src/shared/util.py"]})
    L.acquire(log, cfg, "A", holder="a")
    with pytest.raises(L.LeaseError, match="overlaps"):
        L.acquire(log, cfg, "B", holder="b")


def test_reacquiring_your_own_lease_renews_it(log, cfg):
    seed(log)
    L.acquire(log, cfg, "T1", holder="a")
    time.sleep(0.01)
    again = L.acquire(log, cfg, "T1", holder="a")
    assert again.holder == "a"
    assert [e.kind for e in log.read_all()].count("lease.renewed") == 1


def test_an_expired_lease_is_never_stolen_silently(log, cfg):
    seed(log)
    cfg.lease.ttl_s = 1
    cfg.lease.grace_s = 0
    L.acquire(log, cfg, "T1", holder="crashed")
    time.sleep(1.2)
    with pytest.raises(L.LeaseError, match="NOT stolen automatically"):
        L.acquire(log, cfg, "T1", holder="new")
    lz = L.acquire(log, cfg, "T1", holder="new", force=True)
    assert lz.holder == "new", "--force is the deliberate override"


def _grab(args):
    root, name = args
    cfg = Config.load()
    lg = EventLog(Path(root), name)
    try:
        L.acquire(lg, cfg, "T1", holder=name)
        return name
    except L.LeaseError:
        return ""


def test_only_one_of_many_racing_agents_wins(repo):
    """The race the transaction exists to prevent, run with real processes."""
    log = EventLog(repo, "seed")
    seed(log)
    with mp.Pool(10) as pool:
        winners = [w for w in pool.map(_grab, [(str(repo), f"a{i}") for i in range(10)]) if w]
    assert len(winners) == 1, f"expected exactly one winner, got {winners}"
    st = fold(EventLog(repo, "r").read_all())
    assert st.items["T1"].lease.holder == winners[0]


def test_recovery_reports_a_crashed_agents_uncommitted_work(repo, cfg):
    log = EventLog(repo, "a")
    seed(log, ["T1"])
    wt = W.create(repo, cfg, "T1")
    (wt.path / "finished_work.py").write_text("# exists nowhere else\n")
    L.acquire(log, cfg, "T1", holder="crashed", worktree=str(wt.path), branch=wt.branch)
    found = L.scan(log, cfg, repo, now=time.time() + 10**6)
    assert len(found) == 1
    r = found[0]
    assert r.kind == "expired_lease" and r.salvageable
    assert r.dirty_files == 1
    assert "INSPECT FIRST" in r.advice


def test_sweep_never_touches_salvageable_work(repo, cfg):
    log = EventLog(repo, "a")
    seed(log, ["T1", "T2"])
    wt1 = W.create(repo, cfg, "T1")
    (wt1.path / "work.py").write_text("x\n")
    W.create(repo, cfg, "T2")  # clean tree
    cfg.lease.ttl_s, cfg.lease.grace_s = 0, 0
    for t, w in (("T1", wt1.path), ("T2", repo / ".." / "x")):
        L.acquire(log, cfg, t, holder="ghost", worktree=str(w))
    time.sleep(0.05)
    L.sweep(log, cfg, repo, apply=True)
    st = fold(log.read_all())
    assert st.items["T1"].lease is not None, "salvageable work must keep its claim"


def test_release_is_recorded_and_frees_the_item(log, cfg):
    seed(log)
    L.acquire(log, cfg, "T1", holder="a")
    assert L.release(log, "T1", holder="a")
    assert fold(log.read_all()).items["T1"].lease is None
    assert L.acquire(log, cfg, "T1", holder="b").holder == "b"


# -- regression tests for the recovery-reports-no-worktree defect ----------------------


def test_renewing_your_own_lease_does_not_drop_the_worktree(repo, cfg):
    """`claim` attaches the worktree by re-acquiring; the renew path must carry it.

    Mutation-verified: reverting the `upd` block in `lease.acquire` so renewal appends
    only {at, holder} turns this red, and with it the recovery test below.
    """
    log = EventLog(repo, "a")
    seed(log, ["T1"])
    L.acquire(log, cfg, "T1", holder="a")
    L.acquire(log, cfg, "T1", holder="a", worktree="/tmp/wt-T1", branch="orchard/T1")
    lease = fold(log.read_all()).items["T1"].lease
    assert lease.worktree == "/tmp/wt-T1", "the worktree was silently dropped on renew"
    assert lease.branch == "orchard/T1"


def test_a_plain_heartbeat_never_clears_an_attachment(repo, cfg):
    """The inverse error: a bare renewal must not blank the fields it omits."""
    log = EventLog(repo, "a")
    seed(log, ["T1"])
    L.acquire(log, cfg, "T1", holder="a", worktree="/tmp/wt-T1", branch="orchard/T1")
    L.renew(log, "T1", holder="a")
    lease = fold(log.read_all()).items["T1"].lease
    assert lease.worktree == "/tmp/wt-T1"


def test_recovery_never_says_nothing_to_salvage_over_real_work(repo, cfg):
    """The catastrophic case: advising deletion of a tree that holds a day's work.

    Found by the crash-recovery demo scenario. `claim` recorded the lease before the
    worktree existed, the attaching re-acquire hit the renew path which dropped the
    field, and `scan` then read an empty worktree and reported 'nothing to salvage'.
    """
    log = EventLog(repo, "a")
    seed(log, ["T1"])
    wt = W.create(repo, cfg, "T1")
    (wt.path / "irreplaceable.py").write_text("# the only copy\n")
    L.acquire(log, cfg, "T1", holder="crashed")
    L.acquire(log, cfg, "T1", holder="crashed", worktree=str(wt.path), branch=wt.branch)
    rec = L.scan(log, cfg, repo, now=time.time() + 10**6)[0]
    assert rec.worktree == str(wt.path), "recovery lost the worktree path"
    assert rec.salvageable is True
    assert rec.dirty_files == 1
    assert "nothing to salvage" not in rec.advice


def test_a_completed_item_cannot_be_reclaimed_by_a_stale_agent(repo, cfg):
    """Duplicate work is the failure a queue exists to prevent.

    An agent chooses what to claim from a snapshot; between that snapshot and the
    acquire, another agent can finish the item. Without this guard the second agent
    re-does completed work. Found by the 8-process stress test, which recorded 50
    claims against 32 tasks. Mutation-verified: deleting the `it.state == DONE` guard
    in `lease.acquire` makes this red.
    """
    log = EventLog(repo, "a")
    seed(log, ["T1"])
    L.acquire(log, cfg, "T1", holder="fast")
    log.append("item.completed", "T1", {"sha": "abc1234"})
    L.release(log, "T1", holder="fast")

    with pytest.raises(L.LeaseError, match="already done") as exc:
        L.acquire(log, cfg, "T1", holder="stale")
    assert "abc1234" in str(exc.value), "the refusal should name the commit"

    # Deliberate re-opening stays possible; the guard is against accident, not intent.
    assert L.acquire(log, cfg, "T1", holder="stale", force=True).holder == "stale"


def test_recovery_is_correct_on_a_repo_whose_default_branch_is_not_main(tmp_path, cfg):
    """A drifted duplicate of the branch resolver advised DELETING unmerged work.

    `lease.py` had grown its own `_default_branch`, which fell back to the literal
    string "HEAD" where `worktree.default_branch` falls back to the current branch
    name. On any repo not on main/master that made the unmerged count
    `rev-list HEAD..HEAD` = 0, so a worktree holding a real commit was reported
    "clean and fully merged — safe to remove", and `recover --apply` targets exactly
    the leases it judges unsalvageable.

    Found by roborev's duplication analysis. Fixed by DELETING the duplicate rather
    than repairing it — two implementations of one operation always drift again.
    Mutation-verified: restoring `_default_branch` makes this red.
    """
    repo = tmp_path / "trunkrepo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "trunk", str(repo)], check=True)
    for k, v in (("user.email", "t@t"), ("user.name", "t"), ("commit.gpgsign", "false")):
        subprocess.run(["git", "-C", str(repo), "config", k, v], check=True)
    (repo / "a.txt").write_text("x\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "init"], check=True)

    log = EventLog(repo, "a")
    seed(log, ["T1"])
    wt = W.create(repo, cfg, "T1")
    (wt.path / "real_work.py").write_text("# committed but NOT merged\n")
    subprocess.run(["git", "-C", str(wt.path), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(wt.path), "commit", "-qm", "work"], check=True)
    L.acquire(log, cfg, "T1", holder="crashed", worktree=str(wt.path), branch=wt.branch)

    rec = L.scan(log, cfg, repo, now=time.time() + 10**6)[0]
    assert rec.unmerged_commits == 1, (
        f"branch resolution is wrong on a 'trunk' repo: counted "
        f"{rec.unmerged_commits} unmerged commits"
    )
    assert rec.salvageable is True
    assert "safe to remove" not in rec.advice, "recovery advised deleting real work"


def test_the_safe_worktree_removal_path_actually_works(repo, cfg):
    """`remove(force=False)` passed an empty-string argv element and always failed.

    `git worktree remove` takes one positional argument; the extra "" made it exit 129
    with a usage message, so the SAFE removal path could never succeed — which trains
    an operator to reach for --force, the flag that bypasses the dirty/unmerged check
    this function exists to perform. Found by adversarial review with a probe.
    Mutation-verified: restoring the empty-string element makes this red.
    """
    log = EventLog(repo, "a")
    seed(log, ["T1"])
    wt = W.create(repo, cfg, "T1")
    assert wt.path.exists()
    r = W.remove(repo, cfg, wt)  # clean tree, no --force
    assert r.ok, f"the safe removal path failed: exit {r.code}: {r.err or r.out}"
    assert not wt.path.exists(), "the worktree was not actually removed"


def test_removal_still_refuses_to_destroy_unmerged_work(repo, cfg):
    """The inverse: fixing the argv must not turn remove into a destroyer."""
    log = EventLog(repo, "a")
    seed(log, ["T2"])
    wt = W.create(repo, cfg, "T2")
    (wt.path / "unsaved.py").write_text("# only copy\n")
    r = W.remove(repo, cfg, wt)
    assert not r.ok and "refusing to remove" in r.err
    assert (wt.path / "unsaved.py").exists()


def test_suggested_alternatives_are_exactly_what_the_scheduler_would_offer(repo, cfg):
    """A refusal must not recommend an item the scheduler will also refuse.

    `_alternatives` had forked the readiness rules and drifted: it ignored
    `schedule.unknown_dep_policy`, so an agent refused T1 was told to take T2 — whose
    dependency is a typo — and was then refused again. Found by roborev's architecture
    analysis. Fixed by delegating to `schedule.plan` instead of re-deriving.
    Mutation-verified: restoring the local rule makes this red.
    """
    from orchard.schedule import plan

    log = EventLog(repo, "a")
    log.append("phase.added", "P1", {})
    log.append("task.added", "T1", {"parent": "P1", "globs": ["src/a/*"]})
    log.append(
        "task.added",
        "T2",
        {"parent": "P1", "globs": ["src/b/*"], "needs": ["T_TYPO_DOES_NOT_EXIST"]},
    )
    log.append("task.added", "T3", {"parent": "P1", "globs": ["src/c/*"]})
    L.acquire(log, cfg, "T1", holder="agent-a")
    with pytest.raises(L.LeaseError) as exc:
        L.acquire(log, cfg, "T1", holder="agent-b")

    st = fold(log.read_all())
    offered = {i.id for i in plan(st, cfg, phase="P1", agent="agent-b").ready}
    assert set(exc.value.alternatives) <= offered, (
        f"suggested {exc.value.alternatives} but the scheduler only offers {offered}"
    )
    assert "T3" in exc.value.alternatives
    assert "T2" not in exc.value.alternatives, "recommended an item with a broken dep"


def test_an_unmeasurable_worktree_is_never_reported_as_safe_to_remove(repo, cfg):
    """Unknown must not collapse into clean.

    `_measure` encodes measurement failure as -1, and `salvageable = dirty > 0 or
    unmerged > 0` made that False — so a worktree git could not read (broken gitdir,
    git absent, NFS stall) was reported "clean and fully merged — safe to remove", and
    `sweep(apply=True)` acts on exactly that flag. Found by roborev's architecture
    analysis. Mutation-verified: making `salvageable` two-valued again turns this red.
    """
    log = EventLog(repo, "a")
    seed(log, ["T1"])
    wt = W.create(repo, cfg, "T1")
    (wt.path / "unshipped.py").write_text("# the only copy\n")
    L.acquire(log, cfg, "T1", holder="crashed", worktree=str(wt.path), branch=wt.branch)

    # Break the worktree's gitdir link so git cannot answer, but the files remain.
    (wt.path / ".git").write_text("gitdir: /nonexistent/broken\n")

    rec = L.scan(log, cfg, repo, now=time.time() + 10**6)[0]
    assert rec.salvageable is None, f"unknown collapsed to {rec.salvageable!r}"
    assert "COULD NOT MEASURE" in rec.advice
    assert "safe to remove" not in rec.advice

    L.sweep(log, cfg, repo, apply=True)
    assert fold(log.read_all()).items["T1"].lease is not None, "an unmeasurable worktree was swept"


def test_the_captured_diff_includes_untracked_files(repo, cfg):
    """A reviewer handed a diff without the new test file reports "there are no tests".

    `git diff` omits untracked files entirely, so the regression test an agent just
    wrote is invisible to the critic gate — producing a finding that is correct given
    its input and wrong given the facts. Found by mining this project's own lessons
    corpus. Mutation-verified: setting include_untracked=False makes this red.
    """
    (repo / "existing.py").write_text("x = 1\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "base"], check=True)

    (repo / "existing.py").write_text("x = 2\n")  # tracked modification
    (repo / "test_new_regression.py").write_text("def test_x(): assert True\n")  # NEW

    diff = W.capture_diff(repo)
    assert "existing.py" in diff, "the tracked modification is missing"
    assert "test_new_regression.py" in diff, (
        "the untracked regression test is missing from the diff a reviewer would see"
    )
    ok, missing = W.diff_covers_everything(repo, diff)
    assert ok, f"diff omits: {missing}"


def test_capturing_a_diff_leaves_the_index_untouched(repo, cfg):
    """Intent-to-add must be undone: a review that stages files changes what the next
    commit contains, which is a side effect no reviewer should have."""
    (repo / "a.py").write_text("x\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "base"], check=True)
    (repo / "untracked.py").write_text("y\n")
    before = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain"], capture_output=True, text=True
    ).stdout
    W.capture_diff(repo)
    after = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain"], capture_output=True, text=True
    ).stdout
    assert before == after, f"capture_diff changed the index:\n{before!r}\n{after!r}"
