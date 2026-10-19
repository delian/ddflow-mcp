"""The `owner` column of a record query asks `Lease.live`, so the clock and a recorded
expiry both end ownership (bug Bcb17612395: it read `expired_at` and ignored the clock)."""

from __future__ import annotations

import time
from types import SimpleNamespace as NS

from ddflow.core.records import Lease
from ddflow.services.doctypes import recordquery as RQ


def _item(**kw) -> NS:
    now = time.time()
    return NS(lease=Lease(holder="a1", acquired_at=now, renewed_at=now, ttl_s=3600, **kw))


def test_a_live_lease_names_its_holder():
    assert RQ._item_owner(_item()) == "a1"


def test_a_lease_past_its_ttl_names_nobody():
    it = _item()
    it.lease.renewed_at = it.lease.acquired_at = time.time() - 7200
    assert RQ._item_owner(it) == ""


def test_a_recorded_expiry_names_nobody():
    assert RQ._item_owner(_item(expired_at="2026-09-01T00:00:00Z")) == ""


def test_no_lease_names_nobody():
    assert RQ._item_owner(NS(lease=None)) == ""
