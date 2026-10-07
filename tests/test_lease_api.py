"""One lease API (B-uni-lease-api): `Lease.live` is the only liveness answer, and a
release, a transfer and a lost-contest release are written by `services.leases`."""

from __future__ import annotations

import ast
import re
import time
from pathlib import Path

import pytest
from conftest import run_cli

from ddflow.config import Config
from ddflow.core.model import Lease, fold
from ddflow.infra.log import EventLog
from ddflow.services import leases as L

GRACE = 120


def _lease(**kw) -> Lease:
    return Lease(holder="a", acquired_at=1000.0, renewed_at=1000.0, ttl_s=100, **kw)


#: (name, lease kwargs, now, expired by the clock alone, live)
TABLE = [
    ("fresh", {}, 1050.0, False, True),
    ("at the ttl", {}, 1100.0, False, True),
    ("past ttl", {}, 1150.0, True, False),  # no grace; see the grace test below
    ("recorded expiry", {"expired_at": "t", "ttl_s": 0}, 1001.0, True, False),
]


@pytest.mark.parametrize(("name", "kw", "now", "clock_expired", "live0"), TABLE)
def test_live_table_with_no_grace(name, kw, now, clock_expired, live0):
    lease = Lease(holder="a", acquired_at=1000.0, renewed_at=1000.0, **{"ttl_s": 100, **kw})
    assert lease.expired(now) is clock_expired, name
    assert lease.live(now) is live0, name
    assert lease.live(now) is (not lease.expired_at and not lease.expired(now))


def test_grace_extends_a_lapsed_lease_but_never_a_recorded_expiry():
    lapsed = _lease()
    assert lapsed.live(1150.0, GRACE)  # past the TTL, inside the grace
    assert not lapsed.live(1300.0, GRACE)  # past both
    recorded = _lease(expired_at="2026-10-07T00:00:00Z")
    recorded.ttl_s = 0  # what the fold does on `lease.expired`
    assert not recorded.expired(1050.0, GRACE), "the clock still calls it inside its grace"
    assert not recorded.live(1050.0, GRACE), "a recorded expiry ends the lease at once"


def _claimed(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", "a.py")
    assert run_cli(repo, "claim", "T1")[0] == 0
    log = EventLog(repo)
    return log, Config.load(repo), fold(log.read_all(), strict=False).items["T1"].lease


def test_a_recorded_expiry_leaves_active_leases_at_once(repo):
    log, cfg, _ = _claimed(repo)
    now = time.time()
    st = fold(log.read_all(), strict=False)
    assert "T1" in st.active_leases(now, cfg.lease.grace_s)
    assert L.expire(log, "T1", reason="test")
    st = fold(log.read_all(), strict=False)
    assert "T1" not in st.active_leases(now, cfg.lease.grace_s)
    assert "T1" in st.expired_leases(now, cfg.lease.grace_s)


def test_acquire_on_a_recorded_expiry_names_the_recorded_expiry(repo):
    """Inside the grace window the old clock-only test called the lease live and refused
    with 'held by ...', never reaching the message that says how to take it over."""
    log, cfg, _ = _claimed(repo)
    assert L.expire(log, "T1", reason="test")
    with pytest.raises(L.LeaseError, match="expiry was recorded"):
        L.acquire(log, cfg, "T1", holder="someone-else", globs=["a.py"])


def test_release_claim_and_transfer_write_the_events(repo):
    log, _cfg, lease = _claimed(repo)
    with log.transaction():
        L.transfer(log, "T1", lease, to="new", note="moved", kind="task", now=time.time())
    events = [e for e in log.read_all() if e.subject == "T1"]
    released = [e for e in events if e.kind == "lease.released"][-1]
    assert released.data == {
        "holder": lease.holder,
        "event": lease.event,
        "by": "new",
        "note": "moved",
        "transfer": True,
    }
    st = fold(log.read_all(), strict=False)
    new = st.items["T1"].lease
    assert new.holder == "new" and new.worktree == lease.worktree and new.globs == lease.globs
    assert st.items["T1"].state == "running"  # a transfer never reopens the item


def test_release_claim_without_transfer_has_no_transfer_flag(repo):
    log, _cfg, lease = _claimed(repo)
    with log.transaction():
        L.release_claim(log, "T1", holder=lease.holder, event=lease.event, note="n")
    released = [e for e in log.read_all() if e.kind == "lease.released"][-1]
    assert "transfer" not in released.data and released.data["by"] == log.agent_id


def test_no_caller_outside_the_lease_modules_reads_the_clock_or_writes_a_release():
    """Every liveness question goes through `Lease.live`, every release through
    `services.leases`: a second copy of either is how three answers came to exist."""
    root = Path(__file__).resolve().parents[1] / "ddflow"
    home = root / "core" / "records.py"
    # The home defines `expired` and asks it exactly once, inside `live`.
    assert home.read_text().count(".expired(") == 1
    bad = []
    for path in root.rglob("*.py"):
        if path == home:
            continue
        text = path.read_text()
        for n, line in enumerate(text.splitlines(), 1):
            if re.search(r"\.expired\(", line) and "def expired" not in line:
                bad.append(f"{path.relative_to(root)}:{n}: {line.strip()}")
    # A recorded expiry is read as the CAUSE of a refusal in services/leases.py, and folded
    # in core/; anywhere else it is a liveness test that ignores the clock.
    for path in root.rglob("*.py"):
        if "core" in path.relative_to(root).parts or path == root / "services" / "leases.py":
            continue
        for n, line in enumerate(path.read_text().splitlines(), 1):
            if ".expired_at" in line:
                bad.append(f"{path.relative_to(root)}:{n}: {line.strip()}")
    assert not bad, "use Lease.live(now, grace):\n" + "\n".join(bad)
    raw = []
    for path in root.rglob("*.py"):
        if path.name == "leases.py" and path.parent.name == "services":
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "append"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value in {"lease.released", "lease.acquired"}
            ):
                raw.append(f"{path.relative_to(root)}:{node.lineno}")
    assert not raw, f"append a lease.released/acquired through services.leases: {raw}"


def _lapsed_unrecorded(repo, at: float):
    """T1 claimed by 'gone' at ``at`` with the shipped TTL and no recorded expiry."""
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", "a.py")
    log = EventLog(repo)
    log.append(
        "lease.acquired", "T1", {"holder": "gone", "at": at, "ttl_s": 1800, "globs": ["a.py"]}
    )
    return fold(log.read_all(), strict=False), Config.load(repo)


def test_dedupe_does_not_call_a_lapsed_unrecorded_claim_held(repo):
    """A claim whose TTL and grace ran out is dead whether or not recovery recorded it:
    `record_state` must not say "claimed by", and the record must be extendable."""
    from ddflow.api import _dedupe as DD

    st, cfg = _lapsed_unrecorded(repo, at=time.time() - 10 * 86400)
    now, grace = time.time(), cfg.lease.grace_s
    where = DD.record_state(st, "T1", "task", now=now, grace_s=grace)[1]
    assert not where.startswith("claimed by"), where
    assert DD.extendable(st, "T1", "task", now=now, grace_s=grace)


def test_dedupe_calls_a_live_claim_held(repo):
    from ddflow.api import _dedupe as DD

    st, cfg = _lapsed_unrecorded(repo, at=time.time())
    where = DD.record_state(st, "T1", "task", now=time.time(), grace_s=cfg.lease.grace_s)[1]
    assert where == "claimed by gone"
    assert not DD.extendable(st, "T1", "task", grace_s=cfg.lease.grace_s)


def test_a_duplicate_pointing_at_a_claim_names_its_holder_only_while_it_is_live(repo):
    """The notify path: an add that points at a claimed record tells its holder only when
    the claim is live; a lapsed one is extended instead."""
    from ddflow.api import _dedupe as DD

    rec = DD.Record(kind="task", event_kind="task.added", rid="T2", title="t", body="")
    ans = DD.Answer("extends", "T1")
    for at, holder, extension in ((time.time(), "gone", False), (time.time() - 864000, "", True)):
        st, cfg = _lapsed_unrecorded(repo, at=at)
        out = DD._point(st, rec, ans, {"score": 1.0}, DD.Checked(), cfg.lease.grace_s)
        assert bool(out.extension) is extension
        if not extension:
            assert out.notify["holder"] == holder
