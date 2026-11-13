"""A claim refused on files another lease holds says how long they are held and when the
holder last wrote them (DX-lease-release-early).

Original failure: one agent held broad globs for hours only to wait on CI, and a blocked
agent's refusal named the holder but not whether the holder was still making progress --
so the blocked agent had to guess between idling, polling and yelling. The refusal now
carries ``held for N, last write M ago`` per clash, so a holder whose files stopped
changing is plain to see. The ages are fact (the lease's ``acquired_at``, the newest
change in its worktree), never policy.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.core.model import Lease
from ddflow.services import leases as L

HOLDER = "agent-a"
OTHER = "agent-b"


def _lease(now: float, acquired_at: float) -> Lease:
    return Lease(
        holder=HOLDER,
        acquired_at=acquired_at,
        renewed_at=acquired_at,
        ttl_s=1800,
        globs=["src/**"],
    )


def test_clash_report_names_how_long_held_and_last_write():
    """The refuse text says how long the lease is held and how old its newest write is."""
    now = 2 * 3600.0
    lz = _lease(now, acquired_at=0.0)
    text, ids = L.clash_report(
        [("T2", lz, ("src/a.py", "src/**"))],
        now=now,
        activity=lambda _lz: now - 45 * 60,
    )
    assert "held for 2.0h" in text, text
    assert "last write 45 min ago" in text, text
    assert ids == ["T2"]


def test_clash_report_without_activity_says_only_how_long_held():
    """`activity` is optional: without it (or a worktree to read) only the hold time
    appears, and nothing is guessed."""
    now = 2 * 3600.0
    lz = _lease(now, acquired_at=0.0)
    text, _ids = L.clash_report([("T2", lz, ("src/a.py", "src/**"))], now=now, activity=None)
    assert "held for 2.0h" in text, text
    assert "last write" not in text, text


def test_clash_report_backwards_compatible_without_the_extra_args():
    """Old callers that pass only the clash list still get the old clause, with no note."""
    lz = _lease(1234.0, 1234.0)
    text, ids = L.clash_report([("T2", lz, ("src/a.py", "src/**"))])
    assert "held for" not in text, text
    assert ids == ["T2"]


def test_a_claim_refused_on_a_live_lease_names_how_long_it_is_held(repo):
    run_cli(repo, "init", agent=HOLDER)
    code, out, err = run_cli(
        repo, "task", "add", "T1", "--title", "t", "--globs", "src/a.py", agent=HOLDER
    )
    assert code == 0, out + err
    code, out, err = run_cli(repo, "claim", "T1", "--no-worktree", agent=HOLDER)
    assert code == 0, out + err
    code, out, err = run_cli(
        repo, "task", "add", "T2", "--title", "t2", "--globs", "src/a.py", agent=OTHER
    )
    assert code == 0, out + err
    code, out, err = run_cli(repo, "claim", "T2", agent=OTHER)
    assert code == 3, (code, out, err)
    assert "held for" in out + err, out + err
