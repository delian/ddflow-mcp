"""Log scale (B-gap-log-scale): the fold and the log stay fast and give the same answers.

Every optimisation here is pinned against the unoptimised path on a generated log."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from logscale_helpers import _real_copy, any_log  # noqa: F401  (fixtures)

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
    assert 0.38 < share < 0.5  # the real log: 43%


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


def test_fold_is_byte_identical_to_the_asdict_claim_on_a_contested_log(any_log, monkeypatch):  # noqa: F811
    """The flat claim copy changes nothing the fold computes: same State, lease windows
    and contests, on a generated log and on this repository's own log when it has one."""
    from ddflow.core import records
    from ddflow.core.handlers import leases as H

    events = any_log.read_all()
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
    c.lease.grace_s = max(window, 1)  # the window in force is also capped by grace_s
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
    _log, clock = _beats(repo, monkeypatch, window=300, every_s=45, n=60)
    observer = EventLog(repo, "observer")  # another reader of the same shards
    st = fold(observer.read_all(), strict=False)
    lease = st.items["T1"].lease
    truth = clock["t"]
    assert 0 <= truth - lease.renewed_at < 300
    assert lease.live(truth, 120)  # the holder's heartbeat stream keeps it live for an observer


def test_a_renewal_carrying_attachments_is_always_written(repo, monkeypatch):
    monkeypatch.setattr(Config, "load", classmethod(lambda cls, *_a, **_k: _cfg(300)))
    log, _clock = _beats(repo, monkeypatch, window=300, every_s=10, n=2)
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
            for c, d in zip(a.lease_contest, b.lease_contest, strict=True):
                d["lease"]["renewed_at"] = c["lease"]["renewed_at"]
        assert repr(a) == repr(b), k


def test_a_busy_workload_writes_a_fraction_of_its_renewals(tmp_path):
    """The saving on a workload with a heartbeat every 20 s (the hooks' pace). The default
    window is grace_s (120 s): the drift is bounded by it, so the renewal share only falls to
    about a quarter. The acceptance number (under 10% of events) needs the window raised,
    with grace_s and heartbeat_s, to 900 s: pinned here as what that configuration buys."""
    logfixture.generate(tmp_path / ".ddflow" / "events", 20000, seed=1, beat_gap_s=20.0, beats=60)
    events = EventLog(tmp_path).read_all()
    before = sum(e.kind == "lease.renewed" for e in events)

    def share(window: int) -> float:
        small = coalesced(events, window)
        return sum(e.kind == "lease.renewed" for e in small) / len(small)

    assert before / len(events) > 0.4
    assert share(120) < 0.35
    assert share(900) < 0.10


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
    c.lease.renew_log_min_s, c.lease.heartbeat_s, c.lease.grace_s = 300, 1, 900
    assert renew_window(c) == 1
    c.lease.heartbeat_s = 900
    assert renew_window(c) == 300
    c.lease.grace_s = 120  # the slack the lease already allows bounds it too
    assert renew_window(c) == 120


def test_the_renewal_fast_path_folds_identically_to_the_full_window_logic(any_log, monkeypatch):  # noqa: F811
    """A renewal on an item with no contest and nothing displaced skips the claim-window
    pass; the fold is the same as when the pass always runs (a contested log included)."""

    # simplest faithful oracle: the original handler body, kept here verbatim
    def original(st, ev):
        from ddflow.core.handlers.leases import (
            _clashing,
            _join,
            _key,
            _known,
            _late_renewal,
            _widen,
        )
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
        for k in ("worktree", "branch"):
            if k in d:
                setattr(it.lease, k, d[k])
                setattr(it, k, d[k] or getattr(it, k))
        if "globs" in d:
            it.lease.globs = list(d["globs"])
        if "resources" in d:
            it.lease.resources = list(d["resources"])

    from ddflow.core import model

    events = any_log.read_all()
    fast = repr(fold(events, strict=False).items)
    with monkeypatch.context() as m:
        m.setitem(model.HANDLERS, "lease.renewed", original)
        assert repr(fold(events, strict=False).items) == fast


def test_tasks_below_a_phase_equal_the_full_scan_in_the_same_order(any_log):  # noqa: F811
    """`State.tasks(phase)` walks the descendants, not every item; same answer, same order."""
    st = fold(any_log.read_all(), strict=False)
    for iid in [*st.items, "no-such-item"]:
        live = [i for i in st.items.values() if i.kind == "task" and not i.removed]
        wanted = st.descendants(iid)
        assert [i.id for i in st.tasks(iid)] == [i.id for i in live if i.id in wanted]


# -- review findings: probes ---------------------------------------------------------------

import random  # noqa: E402

from ddflow.core.events import Event  # noqa: E402


def _random_lease_log(seed: int) -> list[Event]:
    rng = random.Random(seed)
    evs, lam = (
        [Event("task.added", "T", {"title": "t"}, agent="a", lamport=1, ts="2026-10-01T00:00:00Z")],
        1,
    )
    holders = ["a", "b", "c"]
    acquired: list[str] = []
    for _ in range(rng.randint(5, 40)):
        lam += 1
        h, at = rng.choice(holders), rng.uniform(0, 4000)
        kind = rng.choice(
            ["lease.acquired", "lease.renewed", "lease.renewed", "lease.released", "lease.expired"]
        )
        data: dict = {"holder": h, "at": at, "ttl_s": rng.choice([300, 1800])}
        if kind == "lease.released" and acquired:
            data["event"] = rng.choice(acquired)
        ev = Event(kind, "T", data, agent=h, lamport=lam, ts=f"2026-10-01T00:00:{lam % 60:02d}Z")
        ev = Event(**{**ev.__dict__, "id": ev.compute_id()})
        if kind == "lease.acquired":
            acquired.append(ev.id)
        evs.append(ev)
    return evs


def test_the_renewal_fast_path_is_the_old_fold_on_random_contested_logs(monkeypatch):
    """Late renewals, rival holders, releases and expiries in random order: the guarded
    handler and the original (always run the window pass) fold to the same items."""
    from ddflow.core import model, records
    from ddflow.core.handlers import leases as H

    def original(st, ev):
        it = st.items.get(ev.subject)
        if not it:
            return
        d = ev.data
        stale = it.lease is not None and "at" in d and float(d["at"]) < it.lease.acquired_at
        if "holder" in d and (it.lease is None or d["holder"] != it.lease.holder or stale):
            H._late_renewal(it, d)
            return
        if not it.lease:
            return
        at = float(d.get("at", it.lease.renewed_at))
        if at < it.lease.renewed_at:
            return
        it.lease.renewed_at = at
        H._widen(it, H._key(records._claim(it.lease)), at)
        rivals = H._clashing(H._known(it), records._claim(it.lease))
        if rivals:
            H._join(it, [records._claim(it.lease), *rivals])

    contested = 0
    for seed in range(300):
        evs = _random_lease_log(seed)
        fast = fold(evs, strict=False).items
        contested += any(i.lease_contest or i.displaced for i in fast.values())
        fast_repr = repr(fast)  # before the second fold: folds may share event payload objects
        with monkeypatch.context() as m:
            m.setitem(model.HANDLERS, "lease.renewed", original)
            assert repr(fold(evs, strict=False).items) == fast_repr, seed
    assert contested > 30  # the generator does exercise contests


def test_shardmeta_facts_equal_the_full_stamp_facts(any_log):  # noqa: F811
    from ddflow.core.events import stamp_facts

    full = stamp_facts(any_log.read_all(), "probe", "0.2.3", 1)
    cached = stamp_facts(any_log._stamp_events(), "probe", "0.2.3", 1)
    cached2 = stamp_facts(any_log._stamp_events(), "probe", "0.2.3", 1)  # from the records

    # who stamped a version two agents both stamped depends on read order (cosmetic, already so)
    def same(f):
        return {k: v for k, v in vars(f).items() if k not in ("highest_by", "format_by")}

    assert same(full) == same(cached) == same(cached2)


def test_shardmeta_does_not_launder_a_same_length_rewrite(tmp_path):

    logfixture.generate(tmp_path / ".ddflow" / "events", 400, seed=1)
    log = EventLog(tmp_path, "w")
    log.append("session.note", "S", {"text": "a"})
    shard = log.shard
    before = (shard.stat().st_size, shard.stat().st_mtime_ns)
    meta = log._meta
    ev = log.read_all()[-1]
    import os

    os.utime(shard, ns=(before[1] + 5, before[1] + 5))  # rewritten: same size, newer mtime
    meta.advance(shard, (before[0], before[1] + 5), ev, 10)  # what the append saw just before
    assert meta._rows.get(shard.name) is None  # dropped, so the next read re-reads the shard


def test_an_unreadable_prior_finding_stops_a_chunk_rerun(repo):
    from ddflow.api.review import _rerun_scope
    from ddflow.config import LogConfig
    from ddflow.infra import blobstore as BS

    log = EventLog(repo, "a", log_cfg=LogConfig(evidence_blobs=True))
    log.append("task.added", "T1", {"title": "t", "globs": ["a.py"]})
    ev = log.append(
        "gate.failed",
        "T1",
        {
            "gate": "critic",
            "evidence": {
                "chunk_findings": [{"detail": "d" * 3000, "digest": "x"}],
                "diff_sha": "s",
                "reviewed": [1],
            },
        },
    )
    BS.BlobStore(repo).remove(ev.data["evidence"]["chunk_findings"]["$blob"])
    BS._KNOWN_ROOTS.clear()
    BS.remember(repo)
    it = fold(log.read_all(), strict=False).items["T1"]
    why = _rerun_scope(it, "critic", [], "diff", "1")
    assert isinstance(why, str) and "unavailable" in why


def test_doctor_reports_a_missing_evidence_blob(repo):
    from ddflow.api.reporting.health import doctor
    from ddflow.config import LogConfig
    from ddflow.infra import blobstore as BS

    log = EventLog(repo, "a", log_cfg=LogConfig(evidence_blobs=True))
    log.append("task.added", "T1", {"title": "t", "globs": ["a.py"]})
    ev = log.append("gate.failed", "T1", {"gate": "ci", "evidence": {"tail": "x" * 4000}})
    BS.BlobStore(repo).remove(ev.data["evidence"]["tail"]["$blob"])
    out = doctor(repo)
    assert any("evidence blob" in str(p) for p in out.data.get("problems", [])), out.data


def test_a_log_read_for_another_repository_leaves_no_shard_records(tmp_path):
    logfixture.generate(tmp_path / ".ddflow" / "events", 300, seed=1)
    log = EventLog(tmp_path, cache_writes=False)
    list(log._stamp_events())
    assert log._highest_lamport() > 0
    assert not (tmp_path / ".ddflow" / "local").exists()


def test_doctor_finds_a_missing_blob_that_another_project_still_holds(repo, tmp_path):
    """Readers find a blob by hash in any project this process opened; the doctor checks the
    log's OWN store, so a copy elsewhere does not hide the loss."""
    from ddflow.config import LogConfig
    from ddflow.infra import blobstore as BS
    from ddflow.services import logblobs as LB

    log = EventLog(repo, "a", log_cfg=LogConfig(evidence_blobs=True))
    log.append("task.added", "T1", {"title": "t", "globs": ["a.py"]})
    ev = log.append("gate.failed", "T1", {"gate": "ci", "evidence": {"tail": "x" * 4000}})
    ref = ev.data["evidence"]["tail"]["$blob"]
    other = tmp_path / "other"
    BS.BlobStore(other).put(ref, BS.BlobStore(repo).get_bytes(ref))
    BS.remember(other)
    BS.BlobStore(repo).remove(ref)
    assert LB.full(ev.data["evidence"])["tail"] == "x" * 4000  # found in the other project
    assert LB.problems(repo, log.read_all())  # and still reported for this one
