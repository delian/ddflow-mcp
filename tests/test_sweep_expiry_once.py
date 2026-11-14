"""`sweep(apply=True)` records one lease.expired per lease, not one per pass (WF-lease-expired-once).

The fold keeps an expired lease on its item (so recovery can still see it), and `scan`
reports it again every pass; the sweep called `expire` for each, and 333 of 350
lease.expired events in 14 days were such repeats.
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.api import lifecycle as LC
from ddflow.config import Config
from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.services.leases import recovery


def _expired_empty(repo: Path) -> EventLog:
    """T1 claimed by an agent that wrote nothing and died; its 1 s lease has expired."""
    run_cli(repo, "init")
    cfg = repo / ".ddflow" / "config.toml"
    text = re.sub(r"(?m)^(ttl_s|grace_s) = .*\n", "", cfg.read_text())
    cfg.write_text(text.replace("[lease]\n", "[lease]\nttl_s = 1\ngrace_s = 0\n", 1))
    log = EventLog(repo, "seed")
    log.append("phase.added", "P1", {"title": "p"})
    log.append("task.added", "T1", {"parent": "P1", "title": "t", "globs": ["src/*"]})
    assert LC.claim(repo, "T1", agent="crashed").exit == 0
    time.sleep(2.2)
    return log


def _expiries(repo: Path) -> int:
    return sum(1 for e in EventLog(repo, "r").read_all() if e.kind == "lease.expired")


def test_a_second_sweep_over_an_unchanged_state_records_nothing(repo):
    log = _expired_empty(repo)
    cfg = Config.load(repo)
    first = recovery.sweep(log, cfg, repo, apply=True)
    assert [(r.item, r.salvageable) for r in first] == [("T1", False)]
    assert _expiries(repo) == 1
    again = recovery.sweep(log, cfg, repo, apply=True)
    assert [r.item for r in again] == ["T1"], "the lease is still reported, only not re-recorded"
    recovery.sweep(log, cfg, repo, apply=True)
    assert _expiries(repo) == 1, "one expiry per lease, however many passes"
    assert fold(EventLog(repo, "r").read_all()).items["T1"].lease.expired_at


def test_a_fresh_lease_on_the_same_item_is_expired_again(repo, monkeypatch):
    """Dedup is per lease: a later lease that lapses is a new expiry."""
    log = _expired_empty(repo)
    cfg = Config.load(repo)
    recovery.sweep(log, cfg, repo, apply=True)
    assert _expiries(repo) == 1
    monkeypatch.setattr(
        "ddflow.services.approval.agent_marker", lambda _a="": ""
    )  # a person forcing
    assert LC.claim(repo, "T1", agent="next", force=True).exit == 0
    assert _expiries(repo) == 1, "a takeover records no expiry of its own"
    time.sleep(2.2)
    swept = recovery.sweep(log, cfg, repo, apply=True)
    assert [r.item for r in swept] == ["T1"]
    assert _expiries(repo) == 2, "the final sweep recorded the new lease's expiry"


def test_a_sweep_on_a_stale_snapshot_records_nothing_more(repo, monkeypatch):
    """Two concurrent sweeps: the second folded the log BEFORE the first one's expiry, so
    its own check sees no `expired_at`. The guard inside `expire` (under the lock, on the
    state the append is decided on) is what stops the second event."""
    from ddflow.services.leases import lifecycle

    log = _expired_empty(repo)
    stale = recovery._fold(log)
    assert not stale.items["T1"].lease.expired_at
    assert lifecycle.expire(log, "T1", reason="the other sweep") is True
    assert _expiries(repo) == 1
    monkeypatch.setattr(recovery, "_fold", lambda _log: stale)
    swept = recovery.sweep(log, Config.load(repo), repo, apply=True)
    assert [(r.item, r.salvageable) for r in swept] == [("T1", False)], "the stale view was used"
    assert _expiries(repo) == 1, "one expiry per lease even when the snapshot was stale"
    assert lifecycle.expire(log, "T1", reason="again") is False
