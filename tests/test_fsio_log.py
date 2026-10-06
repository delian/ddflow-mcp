"""The event log on the shared file layer (B-uni-fsio-log): its lock is fsio.file_lock, its
snapshot, clone id and seen-marker are fsio.atomic_write, its digests are core.digest --
with the behaviour each had before."""

from __future__ import annotations

import errno
import hashlib
import json
import os
import threading
import time

import pytest

from ddflow.core import digest as D
from ddflow.infra import fsio
from ddflow.infra import log as L
from ddflow.infra.log import EventLog, clear_parse_cache
from tests.test_log_merged_order import _ev, _put


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setattr(EventLog, "stamp", False)
    monkeypatch.setattr(L, "SNAPSHOT_MIN_EVENTS", 10)
    monkeypatch.delenv(L.SNAPSHOT_ENV, raising=False)
    clear_parse_cache()
    yield
    clear_parse_cache()


# -- the lock --------------------------------------------------------------------------


def test_a_timeout_keeps_the_logs_own_message(tmp_path):
    lock = tmp_path / ".lock"
    with fsio.file_lock(lock), pytest.raises(TimeoutError) as err, L._flock(lock, 0.2):
        pass
    msg = str(err.value)
    assert not isinstance(err.value, fsio.LockTimeout)  # the log's TimeoutError, as before
    assert msg.startswith(f"could not acquire {lock} within 0.2s (waited ")
    assert "check `ddflow doctor`" in msg


def test_a_lock_timeout_inside_the_block_is_not_reported_as_the_logs(tmp_path):
    other = fsio.LockTimeout(tmp_path / "other", 1.0)
    with pytest.raises(fsio.LockTimeout) as err, L._flock(tmp_path / ".lock", 1.0):
        raise other
    assert err.value is other


def test_the_log_lock_retries_every_20ms(tmp_path, monkeypatch):
    waits: list[float] = []
    monkeypatch.setattr(fsio.time, "sleep", waits.append)
    lock = tmp_path / ".lock"
    with fsio.file_lock(lock), pytest.raises(TimeoutError), L._flock(lock, 0.05):
        pass
    assert waits and set(waits) == {0.02}


def test_the_lock_is_free_again_after_the_block(tmp_path):
    lock = tmp_path / ".lock"
    with L._flock(lock, 1.0):
        pass
    with fsio.file_lock(lock, timeout_s=0):
        pass


# -- the snapshot, the clone id and the seen marker ------------------------------------


def test_the_snapshot_is_written_atomically_without_fsync(tmp_path, monkeypatch):
    calls: list[dict] = []
    real = fsio.atomic_write

    def spy(path, data, **kw):
        calls.append({"path": path, **kw})
        real(path, data, **kw)

    monkeypatch.setattr(fsio, "atomic_write", spy)
    log = EventLog(tmp_path, "x")
    _put(log, "a", [_ev("a", i) for i in range(1, 1600)])
    log.read_all()
    snap = log._snapshot_path()
    assert snap.exists()
    assert [c for c in calls if c["path"] == snap] == [{"path": snap, "fsync": False}]
    assert not [p for p in snap.parent.iterdir() if p.name.endswith(".tmp")]


def test_the_snapshot_digest_is_still_sha256_of_its_payload(tmp_path):
    log = EventLog(tmp_path, "x")
    _put(log, "a", [_ev("a", i) for i in range(1, 1600)])
    log.read_all()
    head, _, payload = log._snapshot_path().read_bytes().partition(b"\n")
    assert json.loads(head)["sha256"] == hashlib.sha256(payload).hexdigest()


def _adopted(tmp_path):
    (tmp_path / ".ddflow").mkdir()
    return tmp_path


def test_concurrent_first_uses_agree_on_one_clone_id(tmp_path):
    root = _adopted(tmp_path)
    got: list[str] = []
    threads = [
        threading.Thread(target=lambda: got.append(L._clone_suffix(root))) for _ in range(16)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(set(got)) == 1 and len(got[0]) == 6, got
    assert not [p for p in (root / L.CLONE_ID_FILE).parent.iterdir() if p.name.endswith(".tmp")]


def test_the_clone_id_is_made_where_there_are_no_hard_links(tmp_path, monkeypatch):
    def no_links(*_a, **_k):
        raise OSError(errno.EPERM, "no hard links here")

    monkeypatch.setattr(fsio.os, "link", no_links)
    root = _adopted(tmp_path)
    first = L._clone_suffix(root)
    assert len(first) == 6 and L._clone_suffix(root) == first


def test_the_seen_marker_is_written(tmp_path):
    L._write_seen_marker(tmp_path, "9.9.9")
    marker = tmp_path / L.SEEN_MARKER
    assert '"version": "9.9.9"' in marker.read_text("utf-8")
    assert (marker.parent / ".gitignore").read_text("utf-8") == "*\n"


# -- core.digest's incremental form ----------------------------------------------------


def test_a_hasher_continues_like_hashlib():
    h = D.hasher(memoryview(b"abc")[:2])
    assert h.hexdigest() == hashlib.sha256(b"ab").hexdigest()
    h.update(b"c")
    assert h.hexdigest() == hashlib.sha256(b"abc").hexdigest()
    assert D.content_digest(memoryview(b"abc")) == hashlib.sha256(b"abc").hexdigest()


def test_the_lock_waits_for_a_holder_in_another_process(tmp_path):
    lock = tmp_path / ".lock"
    r, w = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(r)
        with L._flock(lock, 5):
            os.write(w, b"x")
            time.sleep(0.3)
        os._exit(0)
    os.close(w)
    os.read(r, 1)
    t0 = time.monotonic()
    with L._flock(lock, 5):
        waited = time.monotonic() - t0
    os.waitpid(pid, 0)
    os.close(r)
    assert waited >= 0.15


def test_an_exclusive_write_without_hard_links_honours_fsync_false(tmp_path, monkeypatch):
    def no_links(*_a, **_k):
        raise PermissionError("hard links not supported")  # no errno, as some mounts say

    syncs: list[int] = []
    monkeypatch.setattr(fsio.os, "link", no_links)
    monkeypatch.setattr(fsio.os, "fsync", syncs.append)
    fsio.atomic_write(tmp_path / "cache", "x", exclusive=True, fsync=False)
    assert (tmp_path / "cache").read_text() == "x" and syncs == []
