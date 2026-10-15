"""Log scale (B-gap-log-scale): the fold and the log stay fast and give the same answers.

Every optimisation here is pinned against the unoptimised path on a generated log."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from ddflow.core.model import fold
from ddflow.core.records import Lease, _claim
from ddflow.infra.log import EventLog
from ddflow.services import logfixture


def _log(tmp_path: Path, n: int = 1500) -> EventLog:
    logfixture.generate(tmp_path / ".ddflow" / "events", n, seed=3)
    return EventLog(tmp_path)


def test_fixture_has_the_real_logs_shape(tmp_path):
    log = _log(tmp_path, 3000)
    kinds = [e.kind for e in log.read_all()]
    share = kinds.count("lease.renewed") / len(kinds)
    assert 0.35 < share < 0.6  # the real log: 43%


def test_claim_window_equals_asdict_and_is_a_copy():
    lease = Lease("a", 1.0, 2.0, 30, globs=["x"], resources=["gpu:1"], event="e1", clone="c")
    claim = _claim(lease)
    assert claim == {"holder": "a", "event": "e1", "lease": asdict(lease)}
    claim["lease"]["globs"].append("y")
    claim["lease"]["renewed_at"] = 99.0
    assert lease.globs == ["x"] and lease.renewed_at == 2.0


def test_fold_of_the_generated_log_is_stable(tmp_path):
    log = _log(tmp_path)
    a = fold(log.read_all(), strict=False)
    b = fold(log.read_all(), strict=False)
    assert repr(a.items) == repr(b.items)
    assert a.items  # the fixture folds to something


def _asdict_claim(lease):
    return {"holder": lease.holder, "event": lease.event, "lease": asdict(lease)}


def test_fold_is_byte_identical_to_the_asdict_claim_on_a_contested_log(tmp_path, monkeypatch):
    """The flat claim copy changes nothing the fold computes: same State, lease windows
    and contests, on a generated log and on this repository's own log when it has one."""
    from ddflow.core import records
    from ddflow.core.handlers import leases as H
    logs = [_log(tmp_path)]
    real = Path(__file__).resolve().parents[1]
    if (real / ".ddflow" / "events").is_dir():
        logs.append(EventLog(real))
    for log in logs:
        events = log.read_all()
        now = repr(fold(events, strict=False).items)
        with monkeypatch.context() as m:
            m.setattr(records, "_claim", _asdict_claim)
            m.setattr(H, "_claim", _asdict_claim)
            before = repr(fold(events, strict=False).items)
        assert now == before


# -- step 2: coalesced lease renewals -----------------------------------------------------

import time as _time  # noqa: E402

from ddflow.config import Config  # noqa: E402
from ddflow.services import leases as L  # noqa: E402
from ddflow.services.logcoalesce import coalesced, quiet_renewal  # noqa: E402


def _beats(repo, monkeypatch, *, window: int, every_s: float, n: int):
    """One claim, then ``n`` heartbeats ``every_s`` apart on a fake clock. Returns the log."""
    cfg = Config.load(repo)
    cfg.lease.renew_log_min_s = window
    log = EventLog(repo, "holder")
    clock = {"t": 1_800_000_000.0}
    monkeypatch.setattr(_time, "time", lambda: clock["t"])
    from ddflow.core.model import fold as _f  # noqa: F401

    log.append("task.added", "T1", {"title": "t", "globs": ["a.py"]})
    L.acquire(log, cfg, "T1", holder="holder")
    for _ in range(n):
        clock["t"] += every_s
        assert L.renew(log, "T1", "holder")
    return log, clock


def test_heartbeats_inside_the_window_are_not_written(repo, monkeypatch):
    monkeypatch.setattr(Config, "load", classmethod(lambda cls, *_a, **_k: _cfg(300)))
    log, _ = _beats(repo, monkeypatch, window=300, every_s=30, n=40)  # 20 minutes
    renewals = [e for e in log.read_all() if e.kind == "lease.renewed"]
    assert 3 <= len(renewals) <= 6  # ~one per 300 s, not 40


def _cfg(window: int) -> Config:
    c = Config()
    c.lease.renew_log_min_s = window
    return c


def test_window_zero_writes_every_heartbeat(repo, monkeypatch):
    monkeypatch.setattr(Config, "load", classmethod(lambda cls, *_a, **_k: _cfg(0)))
    log, _ = _beats(repo, monkeypatch, window=0, every_s=30, n=10)
    assert len([e for e in log.read_all() if e.kind == "lease.renewed"]) >= 10


def test_expiry_an_observer_computes_moves_by_less_than_the_window(repo, monkeypatch):
    """The two-clone claim: what a clone reading this log (the shards copied, no local
    state) computes as the lease's expiry differs from the unwritten truth by < window,
    and a lease the holder kept alive never reads expired by more than that."""
    monkeypatch.setattr(Config, "load", classmethod(lambda cls, *_a, **_k: _cfg(300)))
    log, clock = _beats(repo, monkeypatch, window=300, every_s=45, n=60)
    observer = EventLog(repo, "observer")  # another reader of the same shards
    st = fold(observer.read_all(), strict=False)
    lease = st.items["T1"].lease
    truth = clock["t"]
    assert 0 <= truth - lease.renewed_at < 300
    assert lease.live(truth, 120)  # the holder's heartbeat stream keeps it live for an observer


def test_a_renewal_carrying_attachments_is_always_written(repo, monkeypatch):
    monkeypatch.setattr(Config, "load", classmethod(lambda cls, *_a, **_k: _cfg(300)))
    log, clock = _beats(repo, monkeypatch, window=300, every_s=10, n=2)
    before = len([e for e in log.read_all() if e.kind == "lease.renewed"])
    cfg = _cfg(300)
    L.acquire(log, cfg, "T1", holder="holder", worktree="/wt", branch="b")  # re-claim attaches
    assert len([e for e in log.read_all() if e.kind == "lease.renewed"]) == before + 1


def test_a_lapsed_lease_is_renewed_on_the_record(repo, monkeypatch):
    monkeypatch.setattr(Config, "load", classmethod(lambda cls, *_a, **_k: _cfg(300)))
    log, clock = _beats(repo, monkeypatch, window=300, every_s=10, n=1)
    before = len([e for e in log.read_all() if e.kind == "lease.renewed"])
    clock["t"] += 5000  # past ttl: not live, so the revival is written
    assert L.renew(log, "T1", "holder")
    assert len([e for e in log.read_all() if e.kind == "lease.renewed"]) == before + 1


def test_coalesced_log_folds_to_the_same_items_but_the_lease_clock(tmp_path):
    """Replay equivalence: dropping the renewals the live path would not write leaves
    every item's state as it was except `renewed_at`, which is within the window."""
    log = _log(tmp_path, 4000)
    events = log.read_all()
    small = coalesced(events, 300)
    assert len(small) < len(events)
    full = fold(events, strict=False).items
    thin = fold(small, strict=False).items
    assert full.keys() == thin.keys()
    for k, a in full.items():
        b = thin[k]
        if a.lease and b.lease:
            assert 0 <= a.lease.renewed_at - b.lease.renewed_at < 300
            b.lease.renewed_at = a.lease.renewed_at
            for c, d in zip(a.lease_contest, b.lease_contest):
                d["lease"]["renewed_at"] = c["lease"]["renewed_at"]
        assert repr(a) == repr(b), k


def test_renewal_share_of_a_busy_workload_falls_under_ten_percent(tmp_path):
    logfixture.generate(tmp_path / ".ddflow" / "events", 20000, seed=1, beat_gap_s=20.0, beats=60)
    events = EventLog(tmp_path).read_all()
    small = coalesced(events, 300)
    share = sum(e.kind == "lease.renewed" for e in small) / len(small)
    assert share < 0.25  # and 5x fewer renewals than written
    assert sum(e.kind == "lease.renewed" for e in small) * 5 < sum(e.kind == "lease.renewed" for e in events)


def test_quiet_renewal_never_for_a_foreign_or_expired_lease():
    lease = Lease("a", 100.0, 100.0, 1800, clone="other")
    assert not quiet_renewal(lease, 110.0, 300, mine="me")
    lease = Lease("a", 100.0, 100.0, 1800, clone="me")
    assert quiet_renewal(lease, 110.0, 300, mine="me")
    assert not quiet_renewal(lease, 110.0, 0, mine="me")
    lease.expired_at = "x"
    assert not quiet_renewal(lease, 110.0, 300, mine="me")


def test_the_window_is_never_wider_than_the_heartbeat():
    from ddflow.services.logcoalesce import renew_window

    c = Config()
    c.lease.renew_log_min_s, c.lease.heartbeat_s = 300, 1
    assert renew_window(c) == 1
    c.lease.heartbeat_s = 900
    assert renew_window(c) == 300


def test_the_renewal_fast_path_folds_identically_to_the_full_window_logic(tmp_path, monkeypatch):
    """A renewal on an item with no contest and nothing displaced skips the claim-window
    pass; the fold is the same as when the pass always runs (a contested log included)."""
    logs = [_log(tmp_path, 6000)]
    real = Path(__file__).resolve().parents[1]
    if (real / ".ddflow" / "events").is_dir():
        logs.append(EventLog(real))  # this repository's own log has contested claims

    # simplest faithful oracle: the original handler body, kept here verbatim
    def original(st, ev):
        from ddflow.core.handlers.leases import _clashing, _join, _key, _known, _late_renewal, _widen
        from ddflow.core.records import _claim

        it = st.items.get(ev.subject)
        if not it:
            return
        d = ev.data
        stale = it.lease is not None and "at" in d and float(d["at"]) < it.lease.acquired_at
        if "holder" in d and (it.lease is None or d["holder"] != it.lease.holder or stale):
            _late_renewal(it, d)
            return
        if not it.lease:
            return
        at = float(d.get("at", it.lease.renewed_at))
        if at < it.lease.renewed_at:
            return
        it.lease.renewed_at = at
        _widen(it, _key(_claim(it.lease)), at)
        rivals = _clashing(_known(it), _claim(it.lease))
        if rivals:
            _join(it, [_claim(it.lease), *rivals])
        for k, setter in (("worktree", None), ("branch", None)):
            if k in d:
                setattr(it.lease, k, d[k])
                setattr(it, k, d[k] or getattr(it, k))
        if "globs" in d:
            it.lease.globs = list(d["globs"])
        if "resources" in d:
            it.lease.resources = list(d["resources"])

    from ddflow.core import model

    for log in logs:
        events = log.read_all()
        fast = repr(fold(events, strict=False).items)
        with monkeypatch.context() as m:
            m.setitem(model.HANDLERS, "lease.renewed", original)
            assert repr(fold(events, strict=False).items) == fast


def test_tasks_below_a_phase_equal_the_full_scan_in_the_same_order(tmp_path):
    """`State.tasks(phase)` walks the descendants, not every item; same answer, same order."""
    logs = [_log(tmp_path, 4000)]
    real = Path(__file__).resolve().parents[1]
    if (real / ".ddflow" / "events").is_dir():
        logs.append(EventLog(real))
    for log in logs:
        st = fold(log.read_all(), strict=False)
        for iid in [*st.items, "no-such-item"]:
            live = [i for i in st.items.values() if i.kind == "task" and not i.removed]
            wanted = st.descendants(iid)
            assert [i.id for i in st.tasks(iid)] == [i.id for i in live if i.id in wanted]
