"""A yield request must name only waiters whose globs STILL overlap the holder's.

`leasebreak.contended` inverts the wait-for graph to say who each holder should yield to.
The wait-for graph recomputes its edges from the globs the waiter ASKED for when its
widening was refused (`lease.waiting`), and those go stale: when the waiter later narrows
its globs back down (gives up the file it could not get), the holder is STILL told to
yield to a waiter that no longer needs its files (B38ebc3e392). The stale edge must not
name the waiter once its current globs no longer overlap the holder's lease."""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.config import Config
from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.services import leasebreak as LB
from ddflow.services import waiting as WG

A, B = "agent-a", "agent-b"


def _two_holders(repo) -> None:
    assert run_cli(repo, "init", agent=A)[0] == 0
    for item, g in (("T1", "src/a.py"), ("T2", "src/b.py")):
        assert run_cli(repo, "task", "add", item, "--title", item, "--globs", g, agent=A)[0] == 0
    assert run_cli(repo, "claim", "T1", "--no-worktree", agent=A)[0] == 0
    assert run_cli(repo, "claim", "T2", "--no-worktree", agent=B)[0] == 0


def _state(repo):
    return fold(EventLog(repo, "reader").read_all(), strict=False)


def _cfg(repo) -> Config:
    return Config.load(repo)


def test_a_waiter_that_narrowed_away_is_not_held_contended(repo):
    _two_holders(repo)  # T1 holds src/a.py (A); T2 holds src/b.py (B)

    # B widens T2 to also want src/a.py (held by T1) -> refused, a wait is recorded; then B
    # narrows T2 back to src/b.py, giving up the src/a.py it could not get.
    assert run_cli(repo, "update", "T2", "--globs", "src/b.py,src/a.py", agent=B)[0] == 3
    assert run_cli(repo, "update", "T2", "--globs", "src/b.py", agent=B)[0] == 0

    st = _state(repo)
    # The refused wait is withdrawn by the narrow (B-update-queue-wait-not-withdrawable): a
    # wait the waiter gave up must not stay on record...
    assert "T2" not in WG.standing(st, _cfg(repo), time.time())
    # ...and it must not mark T1 as contended by T2: T2 gave src/a.py up, so T1 has
    # nobody to yield to and must not be told to.
    assert "T2" not in LB.contended(st, _cfg(repo), time.time()).get("T1", [])


def test_a_waiter_narrowed_down_to_no_globs_is_not_held_contended(repo):
    _two_holders(repo)  # T1 holds src/a.py (A); T2 holds src/b.py (B)

    # Same refused widening, then B empties T2's globs entirely: an empty want overlaps no
    # holder, so a stale edge from it is just as false (review finding: the drop must fire
    # for an empty current-glob set too, not only a disjoint non-empty one).
    assert run_cli(repo, "update", "T2", "--globs", "src/b.py,src/a.py", agent=B)[0] == 3
    code, _out, err = run_cli(repo, "update", "T2", "--globs", "", agent=B)
    assert code == 0, err
    st = _state(repo)
    assert st.items["T2"].globs == []
    # The empty narrow withdraws the wait whose want overlap no holder (the withdrawal,
    # not the contended filter, is what clears the stale edge for it).
    assert "T2" not in WG.standing(st, _cfg(repo), time.time())
    assert "T2" not in LB.contended(st, _cfg(repo), time.time()).get("T1", [])


def test_a_waiter_whose_globs_still_overlap_is_still_named(repo):
    """The positive control: the filter drops a stale edge, not every edge. T2 that is
    NOT narrowed (its current glob is still src/a.py) must stay named for T1."""
    _two_holders(repo)
    # T1 and T2 both declare src/a.py; B's claim is refused, so T2 still wants src/a.py
    # which T1 holds -- a real contention T2 must be named for.
    assert (
        run_cli(repo, "task", "add", "T3", "--title", "t", "--globs", "src/a.py", agent=A)[0] == 0
    )
    assert run_cli(repo, "claim", "T3", "--no-worktree", agent="agent-c")[0] == 3
    st = _state(repo)
    assert "T3" in LB.contended(st, _cfg(repo), time.time()).get("T1", [])
