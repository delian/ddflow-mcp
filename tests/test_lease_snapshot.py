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
        "config_stamp": H.config_stamp(repo),
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
    assert run_cli(repo, "update", "T1", "--globs", "a.py,b.py")[0] == 0
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
        code, msg = CK.verdict(repo, "a7", ["d7/x.py"], repo, repo)
        times.append(time.perf_counter() - t)
        assert (code, msg) == (0, "")
    p95 = statistics.quantiles(times, n=20)[-1]
    assert p95 < BUDGET_P95_S, f"p95 {p95 * 1000:.1f} ms"


def test_concurrent_processes_all_land(repo):
    run_cli(repo, "init")
    names = [f"C{i}" for i in range(5)]
    for n in names:
        run_cli(repo, "task", "add", n, "--title", n, "--globs", f"{n}/**")
    out: dict[str, int] = {}

    def go(n):
        out[n] = run_cli(repo, "claim", n, agent=f"w-{n}")[0]

    threads = [threading.Thread(target=go, args=(n,)) for n in names]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert set(out.values()) == {0}, out
    snap = H.read(repo)
    assert set(names) <= set(snap.leases)
    assert snap.generation >= len(names)  # every claim advanced the counter


def test_a_missed_event_is_rebuilt_not_refreshed(repo, monkeypatch):
    _claimed(repo)
    run_cli(repo, "task", "add", "T2", "--title", "t", "--globs", "b.py")
    run_cli(repo, "task", "add", "T3", "--title", "t", "--globs", "c.py")
    # T2's claim runs in a process that never registered the hook: the snapshot misses it.
    monkeypatch.setenv("DDFLOW_LOCKS_SNAPSHOT", "0")
    assert run_cli(repo, "claim", "T2", agent="other")[0] == 0
    monkeypatch.delenv("DDFLOW_LOCKS_SNAPSHOT")
    assert "T2" not in H.read(repo).leases
    assert run_cli(repo, "claim", "T3", agent="third")[0] == 0
    assert {"T1", "T2", "T3"} <= set(H.read(repo).leases)


def test_a_config_change_after_the_snapshot_makes_it_stale(repo):
    _claimed(repo)
    assert run_cli(repo, "config", "lease.shared_globs", '["docs/**"]')[0] == 0
    snap = H.read(repo)
    assert snap.status == H.STALE and "config" in snap.note


def test_the_age_limit_is_the_knob(repo):
    run_cli(repo, "init")
    assert run_cli(repo, "config", "locks.snapshot_max_age_s", "10")[0] == 0
    run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", "a.py")
    assert run_cli(repo, "claim", "T1")[0] == 0
    assert H.read(repo).status == H.OK
    assert H.read(repo, now=time.time() + 30).status == H.STALE


def test_malformed_content_is_unreadable_never_a_partial_picture(repo):
    bad = [
        {"written_at": float("nan")},
        {"written_at": float("-inf")},
        {"shared": None},
        {"shared": "src/**"},
        {"leases": {"a": "corrupt"}},
        {"leases": {"a": {"holder": "me", "renewed_at": 1, "ttl_s": "nope"}}},
        {"max_age_s": float("inf")},
        {"leases": {"a": {"holder": "me", "renewed_at": 1, "ttl_s": -1}}},
        {"written_at": time.time() + 99999},
        {"config_stamp": "x"},
    ]
    for over in bad:
        _write(repo, **over)
        snap = H.read(repo)
        assert snap.status == H.UNREADABLE, over
        assert "ddflow_heartbeat" in snap.note


def test_project_ignores_a_former_holders_late_renewal_and_foreign_release():
    leases: dict = {}
    S.project(leases, "lease.acquired", "T", {"holder": "a", "globs": ["x"], "at": 10.0}, "a")
    S.project(leases, "lease.renewed", "T", {"holder": "b", "at": 99.0}, "b")
    assert leases["T"]["renewed_at"] == 10.0
    S.project(leases, "lease.renewed", "T", {"holder": "a", "at": 5.0}, "a")
    assert leases["T"]["renewed_at"] == 10.0  # the clock only moves forward
    S.project(leases, "lease.released", "T", {"holder": "b"}, "b")
    assert "T" in leases
    S.project(leases, "item.completed", "T", {"holder": "b"}, "a")
    assert leases == {}  # an item that ended holds nothing, whoever ended it


def test_snapshot_off_writes_nothing(repo):
    run_cli(repo, "init")
    assert run_cli(repo, "config", "locks.snapshot", "false")[0] == 0
    run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", "a.py")
    assert run_cli(repo, "claim", "T1")[0] == 0
    assert not H.path_of(repo).exists()
    assert Config.load(repo).locks.snapshot is False


def test_an_epoch_mtime_config_is_not_an_absent_one(repo, tmp_path):
    cfg = repo / ".ddflow" / "config.toml"
    cfg.parent.mkdir(exist_ok=True)
    absent = H.config_stamp(repo)
    cfg.write_text("")
    import os

    os.utime(cfg, ns=(0, 0))
    assert H.config_stamp(repo) != absent


def test_a_failed_update_removes_the_file_instead_of_leaving_an_old_picture(repo, monkeypatch):
    _claimed(repo)
    assert H.read(repo).status == H.OK
    log = EventLog(repo)
    monkeypatch.setattr(S.Config, "load", staticmethod(lambda root: 1 / 0))
    assert S.apply(log, "lease.renewed", "T1", {"at": time.time()}, "x", 0, 0) is False
    assert H.read(repo).status == H.MISSING


def test_a_shard_that_moved_past_the_event_forces_a_rebuild(repo):
    _claimed(repo)
    log = EventLog(repo)
    size = log.shard.stat().st_size
    # the event claims the shard stood at size-1 -> size-1+1, but the log is larger: not current
    assert not S._continues(
        {
            "leases": {},
            "written_at": time.time(),
            "marks": {log.shard.name: 0},
            "config_stamp": H.config_stamp(repo),
        },
        log,
        Config.load(repo),
        (log.shard.name, 0, size - 1),
        time.time(),
    )
