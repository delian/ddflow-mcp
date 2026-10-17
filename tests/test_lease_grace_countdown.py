"""Ba3425a7aa8: a lease past its TTL but inside the grace is live, and the other countdowns
printed a negative number of seconds (the claim refusal was fixed in Bc99060b8ca)."""

from __future__ import annotations

from conftest import run_cli

from ddflow.config import Config
from ddflow.core import schedule
from ddflow.core.model import fold
from ddflow.infra.log import EventLog


def test_plan_and_show_never_print_a_negative_countdown(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", "a.py")
    assert run_cli(repo, "claim", "T1", agent="a")[0] == 0
    cfg = Config.load(repo)
    state = fold(EventLog(repo).read_all(), strict=False)
    lease = state.items["T1"].lease
    late = lease.renewed_at + lease.ttl_s + 30
    assert 30 < cfg.lease.grace_s

    live = state.active_leases(late, cfg.lease.grace_s)
    assert "T1" in live
    blocked = schedule.item_blocker(
        state, cfg, state.items["T1"], live, agent="b", now=late, in_cycle=set(), cycles=[]
    )
    assert blocked is not None
    why = blocked.detail
    assert "another -" not in why and "grace period" in why

    text = lease.left_text(late)
    assert "-" not in text and "grace period" in text
    assert lease.left_text(lease.renewed_at + 5) == f"{lease.ttl_s - 5}s left"
