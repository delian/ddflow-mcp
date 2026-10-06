"""The signal ring under .ddflow/local/flow and the limit derived from it (B-af-sampler).

Throwaway projects only, with an injected clock and a scripted signal source: nothing here
depends on this machine's load, memory or disk.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
from pathlib import Path

import pytest
from conftest import run_cli

from ddflow.config import Config
from ddflow.core.model import State
from ddflow.infra import signals as SIG
from ddflow.services import flowstate as F

T0 = 1_800_000_000.0
BAD = {"load": 2.0, "memory_free_frac": 0.9, "disk_free_frac": 0.9}  # load over its high mark
GOOD = {"load": 0.05, "memory_free_frac": 0.9, "disk_free_frac": 0.9}


class Clock:
    def __init__(self, t: float = T0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def _ctx(repo: Path, cfg: Config | None = None) -> F.FlowCtx:
    return F.FlowCtx(repo=repo, cfg=cfg or Config(), state=State())


def _lines(repo: Path) -> list[str]:
    return F.ring_path(repo).read_text("utf-8").splitlines()


def test_two_calls_within_the_interval_write_one_sample(repo):
    clock, src = Clock(), SIG.FakeSource(GOOD)
    assert F.sample_if_due(_ctx(repo), src, clock).written
    clock.t += 30
    r = F.sample_if_due(_ctx(repo), src, clock)
    assert not r.written and r.reason == "not due"
    assert len(_lines(repo)) == 1
    clock.t += 31  # past the 60 s interval
    assert F.sample_if_due(_ctx(repo), src, clock).written
    assert len(_lines(repo)) == 2


def test_a_sample_carries_signals_in_flight_and_binding(repo):
    F.sample_if_due(_ctx(repo), SIG.FakeSource(BAD), Clock())
    row = json.loads(_lines(repo)[0])
    assert set(row) >= {"at", "signals", "in_flight", "limit_binding"}
    assert row["signals"]["load_per_core"] == 2.0
    assert row["signals"]["memory_pressure"] == pytest.approx(0.1)
    assert row["in_flight"] == 0 and row["limit_binding"] is False


def test_ten_bad_samples_lower_the_limit_and_name_the_signal(repo):
    clock, src = Clock(), SIG.FakeSource(BAD)
    for _ in range(10):
        F.sample_if_due(_ctx(repo), src, clock)
        clock.t += 60
    d = F.current_limit(_ctx(repo), None, clock)
    assert d.limit < Config().schedule.max_parallel_tasks
    assert d.limited_by == "load_per_core"
    assert d.limit >= Config().schedule.max_parallel_min


def test_healthy_samples_hold_the_start_value(repo):
    clock, src = Clock(), SIG.FakeSource(GOOD)
    for _ in range(10):
        F.sample_if_due(_ctx(repo), src, clock)
        clock.t += 60
    assert F.current_limit(_ctx(repo), None, clock).limit == 4  # never binding: no raise


def test_a_corrupt_line_is_skipped(repo):
    clock, src = Clock(), SIG.FakeSource(BAD)
    for _ in range(5):
        F.sample_if_due(_ctx(repo), src, clock)
        clock.t += 60
    with F.ring_path(repo).open("a", encoding="utf-8") as fh:
        fh.write('{"at": 17, "signals": {"load_per\n')  # truncated by a crash
        fh.write("not json at all\n")
    assert len(F.read_ring(repo)) == 5
    assert F.sample_if_due(_ctx(repo), src, clock).written
    assert len(F.read_ring(repo)) == 6
    assert F.current_limit(_ctx(repo), None, clock).limited_by == "load_per_core"


def test_a_clock_jump_backwards_drops_the_sample(repo):
    clock, src = Clock(), SIG.FakeSource(GOOD)
    F.sample_if_due(_ctx(repo), src, clock)
    clock.t -= 3600
    r = F.sample_if_due(_ctx(repo), src, clock)
    assert not r.written and "earlier" in r.reason
    assert len(_lines(repo)) == 1


@pytest.mark.skipif(os.name == "nt" or os.geteuid() == 0, reason="needs POSIX permissions")
def test_an_unwritable_directory_degrades_to_start_with_a_reason(repo):
    local = repo / ".ddflow" / "local"
    local.mkdir(parents=True)
    local.chmod(0o500)
    try:
        r = F.sample_if_due(_ctx(repo), SIG.FakeSource(BAD), Clock())
        assert not r.written and r.unavailable and "cannot be written" in r.reason
        d = F.current_limit(_ctx(repo), SIG.FakeSource(BAD), Clock())
        assert d.limit == 4 and "cannot be written" in d.reason
    finally:
        local.chmod(0o700)


def test_two_threads_sampling_at_once_leave_a_valid_file(repo):
    clock = Clock()
    errors: list[BaseException] = []

    def worker(n: int) -> None:
        try:
            for i in range(30):
                c = Clock(T0 + 60 * i + n * 0.001)
                F.sample_if_due(_ctx(repo), SIG.FakeSource(GOOD), c)
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    lines = _lines(repo)
    assert all(json.loads(ln) for ln in lines)  # every line parses
    ats = [json.loads(ln)["at"] for ln in lines]
    assert ats and ats == sorted(ats) and len(ats) <= 30
    assert len(set(ats)) == len(ats)
    assert not F.ring_path(repo).with_name("samples.jsonl.lock").exists()
    del clock


def test_the_ring_is_trimmed_to_six_hours(repo):
    clock, src = Clock(), SIG.FakeSource(GOOD)
    F.sample_if_due(_ctx(repo), src, clock)
    clock.t += F.KEEP_S + 120
    F.sample_if_due(_ctx(repo), src, clock)
    assert [json.loads(ln)["at"] for ln in _lines(repo)] == [clock.t]


def test_fixed_mode_is_the_number(repo):
    cfg = Config()
    cfg.schedule.parallel = "fixed"
    cfg.schedule.max_parallel_tasks = 3
    d = F.current_limit(_ctx(repo, cfg), SIG.FakeSource(BAD), Clock())
    assert (d.limit, d.mode) == (3, "fixed")
    assert not F.ring_path(repo).exists()


def test_a_disabled_signal_is_not_recorded(repo):
    cfg = Config()
    cfg.schedule.signals = {**cfg.schedule.signals, "enabled": ["disk_pressure"]}
    F.sample_if_due(_ctx(repo, cfg), SIG.FakeSource(BAD), Clock())
    assert set(json.loads(_lines(repo)[0])["signals"]) == {"disk_pressure"}


def test_the_ring_is_ignored_and_nothing_new_is_tracked(repo):
    assert run_cli(repo, "init")[0] == 0
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "init"], check=True)
    before = subprocess.run(
        ["git", "-C", str(repo), "ls-files"], capture_output=True, text=True, check=True
    ).stdout
    clock = Clock()
    for _ in range(3):
        F.sample_if_due(_ctx(repo), SIG.FakeSource(GOOD), clock)
        clock.t += 60
    assert F.ring_path(repo).exists()
    ignored = subprocess.run(
        ["git", "-C", str(repo), "check-ignore", "-q", str(F.RING)], check=False
    ).returncode
    assert ignored == 0, "init's .gitignore must cover .ddflow/local/"
    status = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain"], capture_output=True, text=True
    ).stdout
    assert status == "", status
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    after = subprocess.run(
        ["git", "-C", str(repo), "ls-files"], capture_output=True, text=True, check=True
    ).stdout
    assert after == before


def test_without_init_the_local_dir_ignores_itself(repo):
    F.sample_if_due(_ctx(repo), SIG.FakeSource(GOOD), Clock())
    status = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain", "-uall"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert ".ddflow" not in status, status  # every untracked file listed, none of ours


def test_commands_sample_through_the_api_loader(repo, monkeypatch):
    """next, brief, claim and heartbeat all load the project through `_load`, which takes a
    sample when one is due -- there is no daemon."""
    monkeypatch.setattr(SIG, "HostSignals", lambda *a, **k: SIG.FakeSource(GOOD))
    assert run_cli(repo, "init")[0] == 0
    from ddflow.api import lifecycle

    lifecycle.brief(repo, agent="a1")
    assert len(F.read_ring(repo)) == 1


def test_doctor_is_silent_on_a_healthy_ring(repo):
    assert run_cli(repo, "init")[0] == 0
    assert F.doctor_notes(repo) == []


def test_doctor_names_a_local_dir_git_does_not_ignore(repo):
    (repo / ".gitignore").write_text("", "utf-8")
    F.sample_if_due(_ctx(repo), SIG.FakeSource(GOOD), Clock())
    (repo / ".ddflow" / "local" / ".gitignore").unlink()
    notes = F.doctor_notes(repo)
    assert any("not ignored by git" in n for n in notes), notes


def test_a_torn_last_line_is_repaired_not_appended_onto(repo):
    clock, src = Clock(), SIG.FakeSource(BAD)
    F.sample_if_due(_ctx(repo), src, clock)
    with F.ring_path(repo).open("a", encoding="utf-8") as fh:
        fh.write('{"at": 1, "signals": {"gate')  # a crash mid-write: no newline
    for _ in range(3):
        clock.t += 60
        assert F.sample_if_due(_ctx(repo), src, clock).written
    assert len(F.read_ring(repo)) == 4
    assert F.ring_path(repo).read_text("utf-8").endswith("\n")


def test_a_semantically_bad_line_is_skipped_not_raised(repo):
    clock, src = Clock(), SIG.FakeSource(BAD)
    F.sample_if_due(_ctx(repo), src, clock)
    with F.ring_path(repo).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"at": T0 + 1, "signals": {"load_per_core": "x"}}) + "\n")
        fh.write(json.dumps({"at": T0 + 2, "signals": {}, "in_flight": "many"}) + "\n")
    assert len(F.read_ring(repo)) == 1
    clock.t += 60
    F.current_limit(_ctx(repo), src, clock)  # does not raise


def test_a_lock_is_removed_only_by_its_owner(repo):
    path = F.ring_path(repo)
    path.parent.mkdir(parents=True)
    lock = path.with_name(path.name + ".lock")
    with F._lock(path) as held:
        assert held
        lock.write_bytes(b"someone-else")  # the lock was taken over meanwhile
    assert lock.read_bytes() == b"someone-else"


def test_a_stale_lock_is_taken_over(repo):
    path = F.ring_path(repo)
    path.parent.mkdir(parents=True)
    lock = path.with_name(path.name + ".lock")
    lock.write_bytes(b"crashed")
    old = lock.stat().st_mtime - F.STALE_LOCK_S - 5
    os.utime(lock, (old, old))
    with F._lock(path) as held:
        assert held
    assert not lock.exists()


def test_the_log_is_read_only_when_a_sample_is_due(repo):
    calls = []

    def events():
        calls.append(1)
        return ()

    clock, src = Clock(), SIG.FakeSource(GOOD)
    ctx = F.FlowCtx(repo=repo, cfg=Config(), state=State(), events=events)
    F.sample_if_due(ctx, src, clock)
    n = len(calls)
    assert n >= 1
    clock.t += 10
    F.sample_if_due(ctx, src, clock)
    assert len(calls) == n  # not due: the log was not read


def test_the_ring_stays_valid_even_when_the_lock_lets_two_writers_in(repo, monkeypatch):
    """The lock throttles; the file's safety does not depend on it (a racy stale-lock
    takeover can admit two writers)."""
    import contextlib

    @contextlib.contextmanager
    def no_lock(path):
        yield True

    monkeypatch.setattr(F, "_lock", no_lock)
    F.sample_if_due(_ctx(repo), SIG.FakeSource(GOOD), Clock())
    with F.ring_path(repo).open("a", encoding="utf-8") as fh:
        fh.write("torn")  # every writer will take the rewrite branch at least once

    results: list = []

    def worker(n: int) -> None:
        for i in range(1, 40):
            c = Clock(T0 + 60 * i + n * 0.001)
            results.append(F.sample_if_due(_ctx(repo), SIG.FakeSource(GOOD), c))

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    rows = F.read_ring(repo)
    assert rows
    assert not [r for r in results if "failed" in r.reason or r.unavailable], results
    for line in F.ring_path(repo).read_text("utf-8").splitlines():
        json.loads(line)  # no torn or interleaved line
    assert not list(F.ring_path(repo).parent.glob("*.tmp"))


def test_a_short_write_still_writes_the_whole_line(repo, monkeypatch):
    real_write = os.write

    def half(fd, data):
        data = bytes(data)
        return real_write(fd, data[: max(1, len(data) // 2)])

    clock, src = Clock(), SIG.FakeSource(GOOD)
    F.sample_if_due(_ctx(repo), src, clock)
    monkeypatch.setattr(F.os, "write", half)
    for _ in range(3):
        clock.t += 60
        assert F.sample_if_due(_ctx(repo), src, clock).written
    monkeypatch.undo()
    assert len(F.read_ring(repo)) == 4


@pytest.mark.skipif(os.name == "nt", reason="POSIX modes")
def test_a_rewrite_keeps_the_rings_mode(repo):
    clock, src = Clock(), SIG.FakeSource(GOOD)
    F.sample_if_due(_ctx(repo), src, clock)
    before = F.ring_path(repo).stat().st_mode & 0o777
    with F.ring_path(repo).open("a", encoding="utf-8") as fh:
        fh.write("torn")
    clock.t += 60
    F.sample_if_due(_ctx(repo), src, clock)  # the rewrite branch
    assert F.ring_path(repo).stat().st_mode & 0o777 == before
