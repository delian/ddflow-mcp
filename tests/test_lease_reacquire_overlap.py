"""Re-claiming an item whose lease lapsed is checked against every live lease (B08b6e40bfb).

Expiry frees the holder's globs for everyone else. kilo-main's lease on B194 lapsed at
15:08; bugfixB claimed overlapping files at 15:17; kilo-main re-acquired B194 at 15:23 and
was granted it, so two live leases covered the same files. The expired-lease refusal tells
the agent to retry with --force, and that --force -- meant to take over the EXPIRED lease
-- also waived the overlap check a fresh claim gets.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.api import lifecycle as LC
from ddflow.core.model import fold
from ddflow.infra.log import EventLog


def _lapsed_then_taken(repo: Path) -> None:
    """T1's lease (holder "first", on src/a.py) lapsed an hour ago; "second" has since
    claimed T2 on the same file, and holds it live."""
    run_cli(repo, "init")
    log = EventLog(repo, "seed")
    log.append("phase.added", "P1", {"title": "p"})
    log.append("task.added", "T1", {"parent": "P1", "title": "t1", "globs": ["src/a.py"]})
    log.append("task.added", "T2", {"parent": "P1", "title": "t2", "globs": ["src/a.py"]})
    first = EventLog(repo, "first")
    first.append(
        "lease.acquired",
        "T1",
        {"holder": "first", "at": time.time() - 3600, "ttl_s": 1800, "globs": ["src/a.py"]},
    )
    first.append("item.started", "T1", {})
    out = LC.claim(repo, "T2", agent="second", no_worktree=True)
    assert out.exit == 0, out.reason


def test_a_forced_reclaim_of_a_lapsed_lease_is_refused_on_a_live_overlap(repo):
    _lapsed_then_taken(repo)
    out = LC.claim(repo, "T1", agent="first", force=True, no_worktree=True)
    assert out.exit == 3, "granted over second's live lease on the same file"
    assert "T2" in out.reason and "second" in out.reason, out.reason
    st = fold(EventLog(repo, "r").read_all())
    live = st.active_leases(time.time())
    assert set(live) == {"T2"}, live


def test_a_takeover_of_another_holders_lapsed_lease_is_refused_on_a_live_overlap(repo):
    _lapsed_then_taken(repo)
    out = LC.claim(repo, "T1", agent="third", force=True, no_worktree=True)
    assert out.exit == 3, out.reason
    assert "T2" in out.reason, out.reason


def test_a_forced_reclaim_with_no_live_overlap_is_still_granted(repo):
    _lapsed_then_taken(repo)
    assert run_cli(repo, "--agent", "second", "release", "T2")[0] == 0
    out = LC.claim(repo, "T1", agent="first", force=True, no_worktree=True)
    assert out.exit == 0, out.reason


def test_a_heartbeat_does_not_revive_a_lapsed_lease_over_a_live_overlap(repo):
    """B0cb404c94e: the heartbeat is the other way back into a lapsed lease."""
    _lapsed_then_taken(repo)
    out = LC.heartbeat(repo, "T1", agent="first")
    assert not out.data.get("renewed"), "revived over second's live lease"
    st = fold(EventLog(repo, "r").read_all())
    assert set(st.active_leases(time.time())) == {"T2"}


def test_a_heartbeat_still_revives_a_lapsed_lease_nobody_overlaps(repo):
    _lapsed_then_taken(repo)
    assert run_cli(repo, "--agent", "second", "release", "T2")[0] == 0
    out = LC.heartbeat(repo, "T1", agent="first")
    assert out.data.get("renewed"), out.reason


def test_a_heartbeat_judges_the_items_widened_globs_too(repo):
    """roborev 1477 #2: the item's globs widened onto the other claim's file after the
    lease lapsed; the catch-up would point the revived lease at them."""
    _lapsed_then_taken(repo)
    assert run_cli(repo, "--agent", "second", "release", "T2")[0] == 0
    log = EventLog(repo, "seed")
    log.append("task.added", "T3", {"parent": "P1", "title": "t3", "globs": ["src/b.py"]})
    assert LC.claim(repo, "T3", agent="second", no_worktree=True).exit == 0
    log.append("task.updated", "T1", {"globs": ["src/a.py", "src/b.py"]})
    out = LC.heartbeat(repo, "T1", agent="first")
    assert not out.data.get("renewed"), "revived onto T3's live src/b.py"


def test_a_forced_fresh_claim_still_waives_the_overlap(repo):
    """roborev 1477 #1: the asymmetry is deliberate. --force on an item nobody held is an
    explicit override of the overlap; on a lapsed lease it was demanded for the expiry
    alone, and an agent following that advice must not override a check it never saw."""
    _lapsed_then_taken(repo)
    EventLog(repo, "seed").append(
        "task.added", "T4", {"parent": "P1", "title": "t4", "globs": ["src/a.py"]}
    )
    assert LC.claim(repo, "T4", agent="third", no_worktree=True).exit == 3
    assert LC.claim(repo, "T4", agent="third", force=True, no_worktree=True).exit == 0
