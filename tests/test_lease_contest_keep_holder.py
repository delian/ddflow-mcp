"""The current holder can win a contest it was never in.

Split from tests/test_lease_contest.py (rule r-small-files); helpers live in lease_contest_support.py.
"""

from __future__ import annotations

from itertools import permutations
from pathlib import Path

import pytest
from helpers import make_event as _ev
from lease_contest_support import (
    _acq,
    _holders,
)

from ddflow.core.model import fold
from ddflow.infra.log import EventLog

# -- the current holder can win a contest it was never in --------------------------------


_TAKEN_OVER = {"alice": (1000.0, 1800), "bob": (1500.0, 1800), "carol": (9000.0, 1800)}


@pytest.mark.parametrize("order", list(permutations(_TAKEN_OVER)), ids="-".join)
@pytest.mark.parametrize(
    ("keep", "released"),
    [("carol", ["alice", "bob"]), ("alice", ["bob", "carol"]), ("bob", ["alice", "carol"])],
)
def test_resolve_can_keep_the_holder_that_took_over_after_a_double_claim(
    repo: Path, order, keep, released
):
    """B-resolve-cannot-keep-holder, FAILED before the fix for carol: Alice and Bob
    claimed at once in two clones; long after both lapsed, Carol took the item over
    cleanly. She is not in the contest -- she overlapped nobody -- and `resolve --keep
    carol` was refused as naming no contestant: the only ways out took the item from
    the live holder and handed it to a lapsed one."""
    log = EventLog(repo, "op")
    log.shard.parent.mkdir(parents=True, exist_ok=True)
    events = [
        _ev("task.added", "T", 1, title="t"),
        *(_acq(2 + i, w, *_TAKEN_OVER[w]) for i, w in enumerate(order)),
    ]
    log.shard.write_text("".join(ev.to_json() + "\n" for ev in events))
    it = fold(log.read_all()).items["T"]
    assert _holders(it.lease_contest) == ["alice", "bob"] and it.lease.holder == "carol"
    from ddflow.api import items

    out = items.resolve(repo, "T", keep=keep, agent="op")
    assert out.exit == 0, out.reason
    assert sorted(out.data["released"]) == released
    it = fold(log.read_all()).items["T"]
    assert it.lease_contest == [] and it.lease.holder == keep
    # The only history left is the kept claim's own window: every claim it met was
    # released, and a lapsed claim holds the item from the resolution on.
    assert [(e["holder"], e["lease"]["acquired_at"]) for e in it.displaced] == [
        (keep, _TAKEN_OVER[keep][0])
    ]
    assert it.lease.acquired_at > _TAKEN_OVER["carol"][0], "a fresh window, not a stretch"
    assert not it.contest_summary()


def test_resolve_names_the_current_holder_among_the_choices(repo: Path):
    """A keep that names nobody lists what it may name -- the current holder too."""
    log = EventLog(repo, "op")
    log.shard.parent.mkdir(parents=True, exist_ok=True)
    events = [
        _ev("task.added", "T", 1, title="t"),
        *(_acq(2 + i, w, *_TAKEN_OVER[w]) for i, w in enumerate(_TAKEN_OVER)),
    ]
    log.shard.write_text("".join(ev.to_json() + "\n" for ev in events))
    from ddflow.api import items

    out = items.resolve(repo, "T", keep="zed", agent="op")
    assert out.exit == 1
    assert "carol" in out.reason and "alice" in out.reason and "bob" in out.reason


def test_keeping_the_current_holder_when_no_lease_contest_is_refused(repo: Path):
    """Nothing to settle: an item with a holder and no contest is not contested."""
    log = EventLog(repo, "op")
    log.shard.parent.mkdir(parents=True, exist_ok=True)
    events = [_ev("task.added", "T", 1, title="t"), _acq(2, "carol", 9000.0, 1800)]
    log.shard.write_text("".join(ev.to_json() + "\n" for ev in events))
    from ddflow.api import items

    assert items.resolve(repo, "T", keep="carol", agent="op").exit == 3
