"""The lease snapshot (LK-hook-snapshot): hooks read a small file, never fold the log."""

from __future__ import annotations

import json
import statistics
import threading
import time

from conftest import run_cli

from ddflow.config import Config
from ddflow.infra.log import EventLog
from ddflow.services.enforce import hooksnap as H
from ddflow.services.leases import snapshot as S
from ddflow.services.swarm import lanehooks_check as CK

BUDGET_P95_S = 0.030


def _claimed(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", "a.py")
    assert run_cli(repo, "claim", "T1")[0] == 0
    return H.read(repo)


def _write(repo, **over):
    doc = {
        "generation": 1,
        "written_at": time.time(),
        "max_age_s": 1800,
        "grace_s": 120,
        "leases": {
            "T1": {"holder": "me", "globs": ["a.py"], "renewed_at": time.time(), "ttl_s": 1800}
        },
        "shared": ["docs/X.md"],
        "new_files": [],
    }
    doc.update(over)
    p = H.path_of(repo)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"schema": 1, "data": doc}))


def test_a_claim_writes_the_snapshot(repo):
    snap = _claimed(repo)
    assert snap.status == H.OK
    assert snap.generation >= 1
    (lease,) = snap.leases.values()
    assert lease["globs"] == ["a.py"]
    assert snap.live_for(lease["holder"], time.time())


def test_release_drops_the_entry_and_bumps_the_generation(repo):
    first = _claimed(repo)
    assert run_cli(repo, "release", "T1")[0] == 0
    after = H.read(repo)
    assert after.leases == {}
    assert after.generation > first.generation


def test_missing_snapshot_is_rebuilt_by_the_next_lease_event(repo):
    _claimed(repo)
    H.path_of(repo).unlink()
    assert H.read(repo).status == H.MISSING
    run_cli(repo, "heartbeat", "T1")  # may be throttled; a claim-class event always writes
    run_cli(repo, "update", "T1", "--globs", "a.py,b.py")
    snap = H.read(repo)
    assert snap.status == H.OK
    assert snap.leases["T1"]["globs"] == ["a.py", "b.py"]


def test_torn_file_reads_unreadable_and_is_replaced(repo):
    _claimed(repo)
    H.path_of(repo).write_text('{"schema": 1, "da')
    snap = H.read(repo)
    assert snap.status == H.UNREADABLE and "ddflow heartbeat" in snap.note
    log = EventLog(repo)
    assert S.apply(log, "lease.renewed", "T1", {"at": time.time()}, "x")
    assert H.read(repo).status == H.OK


def test_stale_snapshot_is_reported_not_trusted(repo):
    _write(repo, written_at=time.time() - 4000)
    snap = H.read(repo)
    assert snap.status == H.STALE
    assert "snapshot_max_age_s" in snap.note and "ddflow_heartbeat" in snap.note
    assert H.read(repo, max_age_s=0).status == H.OK  # 0 = never stale
    assert H.read(repo, now=time.time() - 3000).status == H.OK


def test_newer_format_is_not_read(repo):
    p = H.path_of(repo)
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({"schema": 99, "data": {"leases": {}}}))
    assert H.read(repo).status == H.NEWER


def test_hook_fails_open_and_says_so_when_unusable(repo):
    for setup in (lambda: None, lambda: _write(repo, written_at=1.0)):
        setup()
        code, msg = CK.verdict(repo, "me", ["a.py"], repo, repo)
        assert code == 0
        assert "not checked" in msg and "commit-time" in msg


def test_hook_judges_from_a_fresh_snapshot(repo):
    _write(repo)
    assert CK.verdict(repo, "me", ["a.py"], repo, repo) == (0, "")
    assert CK.verdict(repo, "me", ["docs/X.md"], repo, repo) == (0, "")  # shared
    code, msg = CK.verdict(repo, "me", ["b.py"], repo, repo)
    assert code == CK.REFUSE and "outside the item's lease globs" in msg
    code, msg = CK.verdict(repo, "nobody", ["a.py"], repo, repo)
    assert code == CK.REFUSE and "holds no live lease" in msg


def test_an_expired_lease_in_a_fresh_snapshot_holds_nothing(repo):
    old = time.time() - 5000
    _write(
        repo, leases={"T1": {"holder": "me", "globs": ["a.py"], "renewed_at": old, "ttl_s": 1800}}
    )
    assert CK.verdict(repo, "me", ["a.py"], repo, repo)[0] == CK.REFUSE


def test_hook_read_budget_p95_under_30_ms(repo):
    _write(
        repo,
        leases={
            f"T{i}": {
                "holder": f"a{i}",
                "globs": [f"d{i}/**"] * 5,
                "renewed_at": time.time(),
                "ttl_s": 1800,
            }
            for i in range(60)
        },
    )
    times = []
    for _ in range(200):
        t = time.perf_counter()
        globs, _shared = CK._mine(repo, "a7")
        times.append(time.perf_counter() - t)
        assert globs
    p95 = statistics.quantiles(times, n=20)[-1]
    assert p95 < BUDGET_P95_S, f"p95 {p95 * 1000:.1f} ms"


def test_concurrent_writers_all_land(repo):
    _claimed(repo)
    log = EventLog(repo)
    n = 12
    start = H.read(repo).generation
    errs: list[BaseException] = []

    def go(i):
        try:
            S.apply(
                log,
                "lease.acquired",
                f"C{i}",
                {"holder": f"w{i}", "globs": [f"c{i}"], "at": time.time()},
                "x",
            )
        except BaseException as e:
            errs.append(e)

    threads = [threading.Thread(target=go, args=(i,)) for i in range(n)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    snap = H.read(repo)
    assert not errs
    assert {f"C{i}" for i in range(n)} <= set(snap.leases)
    assert snap.generation == start + n


def test_project_ignores_a_former_holders_late_renewal_and_foreign_release():
    leases: dict = {}
    S.project(leases, "lease.acquired", "T", {"holder": "a", "globs": ["x"], "at": 10.0}, "a")
    S.project(leases, "lease.renewed", "T", {"holder": "b", "at": 99.0}, "b")
    assert leases["T"]["renewed_at"] == 10.0
    S.project(leases, "lease.renewed", "T", {"holder": "a", "at": 5.0}, "a")
    assert leases["T"]["renewed_at"] == 10.0  # the clock only moves forward
    S.project(leases, "lease.released", "T", {"holder": "b"}, "b")
    assert "T" in leases
    S.project(leases, "item.completed", "T", {}, "a")
    assert leases == {}


def test_snapshot_off_writes_nothing(repo):
    run_cli(repo, "init")
    assert run_cli(repo, "config", "locks.snapshot", "false")[0] == 0
    run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", "a.py")
    run_cli(repo, "claim", "T1")
    assert not H.path_of(repo).exists()
    assert Config.load(repo).locks.snapshot is False
