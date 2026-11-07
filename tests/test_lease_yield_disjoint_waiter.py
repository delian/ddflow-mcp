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
    # The refused wait is still on record (a widening wait lives and dies with its lease)...
    assert "T2" in WG.standing(st, _cfg(repo), time.time())
    # ...but it must no longer mark T1 as contended by T2: T2 gave src/a.py up, so T1 has
    # nobody to yield to and must not be told to.
    assert "T2" not in LB.contended(st, _cfg(repo), time.time()).get("T1", [])
