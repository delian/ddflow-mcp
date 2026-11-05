"""B-claim-unavailable-fires-past-deps: the standing `claim_unavailable` rule is documented for an
item held by a vanished/unavailable holder, but its predicate was `_true`, so ANY `claim.force` --
including a plain claim of a dep-blocked item with NO holder -- was silently auto-approved over its
unmet dependencies. The predicate now requires a LAPSED lease whose holder is gone."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.config import Config
from ddflow.core.records.items import Item, Lease
from ddflow.infra.log import EventLog
from ddflow.services import approval as AP
from ddflow.services.approvals import consume as C
from ddflow.services.approvals import policy as PO
from ddflow.services.approvals.canon import Canon


@pytest.fixture(autouse=True)
def _a_person(monkeypatch):
    for var in ("DDFLOW_AGENT", *AP.HARNESS_MARKERS):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def proj(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "T1 fixture", "--globs", "t.py")
    return repo


def _cfg(**over):
    cfg = Config()
    for k, v in over.items():
        setattr(cfg.approvals, k, v)
    return cfg


def test_claim_unavailable_requires_a_lapsed_holders_lease():
    """The predicate is not `_true`: it answers ONLY an item actually HELD by a lease whose holder
    is gone (lapsed). A no-lease item and a live-lease item are each a person's call."""
    cfg = _cfg(policy=["claim_unavailable"], policy_max_uses=99)

    no_holder = Item(id="T1", kind="task")
    assert PO.RULES["claim_unavailable"].when(no_holder, "", cfg) is False

    now = time.time()
    live = Item(
        id="T2",
        kind="task",
        lease=Lease(holder="busy", acquired_at=now - 50, renewed_at=now, ttl_s=3600),
    )
    assert PO.RULES["claim_unavailable"].when(live, "", cfg) is False

    gone = Item(
        id="T3",
        kind="task",
        lease=Lease(holder="gone", acquired_at=0.0, renewed_at=0.0, ttl_s=1),
    )
    assert PO.RULES["claim_unavailable"].when(gone, "", cfg) is True


def test_claim_force_over_an_item_with_no_holder_is_refused_not_auto_approved(proj):
    """B-claim-unavailable-fires-past-deps: a `claim.force` whose item has NO lease is NOT
    auto-approved by the standing `claim_unavailable` rule -- a person is asked instead. Under the
    old `_true` predicate this fired and silently force-granted a worktree over unmet dependencies."""
    log = EventLog(proj, "bot")
    claim = Canon(verb="claim.force", agent="bot", item="T1", state="s")
    cfg = _cfg(policy=["claim_unavailable"], policy_max_uses=99)
    got = C.operator_gate(log, cfg, claim)
    assert (
        not got.ok and got.ask_id
    )  # a person is asked, never a standing `policy:claim_unavailable`
