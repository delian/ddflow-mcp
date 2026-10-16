"""The agent whose lease lapsed resumes it with a plain claim (DX-lease-lapse-reclaim)."""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_lease_reacquire_overlap import _lapsed_then_taken

from conftest import run_cli
from ddflow.api import lifecycle as LC
from ddflow.core.model import fold
from ddflow.infra.log import EventLog


def test_the_holder_resumes_its_lapsed_lease_without_force(repo):
    _lapsed_then_taken(repo)
    assert run_cli(repo, "--agent", "second", "release", "T2")[0] == 0
    out = LC.claim(repo, "T1", agent="first", no_worktree=True)
    assert out.exit == 0, out.reason
    assert out.data["globs"] == ["src/a.py"], out.data
    st = fold(EventLog(repo, "r").read_all())
    assert set(st.active_leases(time.time())) == {"T1"}


def test_the_holder_resuming_over_a_taken_file_is_refused_and_told_which(repo):
    _lapsed_then_taken(repo)
    out = LC.claim(repo, "T1", agent="first", no_worktree=True)
    assert out.exit == 3, out.reason
    assert "T2" in out.reason and "second" in out.reason and "src/a.py" in out.reason, out.reason
    st = fold(EventLog(repo, "r").read_all())
    assert set(st.active_leases(time.time())) == {"T2"}


def test_another_agent_still_cannot_take_a_lapsed_lease_without_force(repo):
    _lapsed_then_taken(repo)
    assert run_cli(repo, "--agent", "second", "release", "T2")[0] == 0
    out = LC.claim(repo, "T1", agent="third", no_worktree=True)
    assert out.exit == 3 and "EXPIRED" in out.reason, out.reason


def test_a_live_own_lease_is_still_just_renewed(repo):
    _lapsed_then_taken(repo)
    assert run_cli(repo, "--agent", "second", "release", "T2")[0] == 0
    assert LC.claim(repo, "T1", agent="first", no_worktree=True).exit == 0
    again = LC.claim(repo, "T1", agent="first", no_worktree=True)
    assert again.exit == 0, again.reason
    st = fold(EventLog(repo, "r").read_all())
    assert set(st.active_leases(time.time())) == {"T1"}


def _expired_marked(repo):
    """first's lease on T1 lapsed AND was marked expired (recover/sweep), nobody took src/a.py."""
    _lapsed_then_taken(repo)
    assert run_cli(repo, "--agent", "second", "release", "T2")[0] == 0
    EventLog(repo, "sweep").append("lease.expired", "T1", {"holder": "first", "at": time.time()})


def test_a_marked_expired_own_lease_is_resumed_by_claim(repo):
    _expired_marked(repo)
    out = LC.claim(repo, "T1", agent="first", no_worktree=True)
    assert out.exit == 0, out.reason


def test_a_marked_expired_own_lease_is_revived_by_heartbeat(repo):
    _expired_marked(repo)
    out = LC.heartbeat(repo, "T1", agent="first")
    assert out.data.get("renewed"), out.reason


def test_heartbeat_from_the_leased_tree_revives_a_lapsed_lease_for_its_holder(repo):
    run_cli(repo, "init")
    run_cli(repo, "config", "lease.ttl_s", "1")
    run_cli(repo, "config", "lease.grace_s", "0")
    log = EventLog(repo, "seed")
    log.append("phase.added", "P1", {"title": "p"})
    log.append("task.added", "T1", {"parent": "P1", "title": "t", "globs": ["src/a.py"]})
    out = LC.claim(repo, "T1", agent="first")
    assert out.exit == 0, out.reason
    tree = Path(out.data["worktree"])
    time.sleep(2.5)
    assert not fold(EventLog(repo, "r").read_all()).items["T1"].lease.live(time.time(), 0)
    # the tree's derived identity is not "first": it speaks for the lease's holder
    beat = LC.heartbeat(repo, "T1", agent="other", called_from=tree)
    assert beat.data.get("renewed"), beat.reason
