"""The file layer (B-uni-fsio-core): atomic_write, file_lock and core.digest.

Real files in a temporary directory; concurrency through real threads and processes.
"""

from __future__ import annotations

import errno
import hashlib
import os
import stat
import threading
import time
from pathlib import Path

import pytest
from conftest import run_cli

from ddflow.core.digest import content_digest as digest
from ddflow.infra import fsio, tomlcfg


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def _leftovers(d: Path) -> list[str]:
    return sorted(p.name for p in d.iterdir() if p.name.endswith(".tmp"))


# -- atomic_write --------------------------------------------------------------------


def test_writes_text_and_bytes(tmp_path):
    fsio.atomic_write(tmp_path / "a.txt", "héllo\n")
    assert (tmp_path / "a.txt").read_text("utf-8") == "héllo\n"
    fsio.atomic_write(tmp_path / "b.bin", b"\x00\xff")
    assert (tmp_path / "b.bin").read_bytes() == b"\x00\xff"
    fsio.atomic_write(tmp_path / "deep" / "er" / "c.txt", "x")  # parents made
    assert (tmp_path / "deep" / "er" / "c.txt").read_text() == "x"
    assert _leftovers(tmp_path) == []


def test_a_new_file_gets_the_mode_write_text_would_give_it(tmp_path):
    (tmp_path / "plain").write_text("x")
    fsio.atomic_write(tmp_path / "atomic", "x")
    assert _mode(tmp_path / "atomic") == _mode(tmp_path / "plain")


def test_an_existing_file_keeps_its_mode(tmp_path):
    for perm in (0o640, 0o755, 0o600):
        p = tmp_path / f"f{perm:o}"
        p.write_text("old")
        p.chmod(perm)
        fsio.atomic_write(p, "new")
        assert (_mode(p), p.read_text()) == (perm, "new")


def test_an_explicit_mode_wins(tmp_path):
    p = tmp_path / "hook"
    p.write_text("old")
    fsio.atomic_write(p, "#!/bin/sh\n", mode=0o755)
    assert _mode(p) == 0o755
    fsio.atomic_write(tmp_path / "secret", "s", mode=0o600)
    assert _mode(tmp_path / "secret") == 0o600


def test_exclusive_refuses_an_existing_file_and_leaves_it_alone(tmp_path):
    p = tmp_path / "once"
    fsio.atomic_write(p, "first", exclusive=True)
    with pytest.raises(FileExistsError):
        fsio.atomic_write(p, "second", exclusive=True)
    assert p.read_text() == "first"
    assert _leftovers(tmp_path) == []


@pytest.mark.parametrize("err", [errno.EPERM, errno.EOPNOTSUPP])
def test_exclusive_works_where_hard_links_do_not(tmp_path, monkeypatch, err):
    def no_links(*_a, **_k):
        raise OSError(err, os.strerror(err))

    monkeypatch.setattr(fsio.os, "link", no_links)
    p = tmp_path / "once"
    fsio.atomic_write(p, "first", exclusive=True, mode=0o640)
    assert (p.read_text(), _mode(p)) == ("first", 0o640)
    with pytest.raises(FileExistsError):
        fsio.atomic_write(p, "second", exclusive=True)
    assert p.read_text() == "first"
    assert _leftovers(tmp_path) == []


def test_a_failed_write_leaves_the_old_file_and_no_temp(tmp_path, monkeypatch):
    p = tmp_path / "config.toml"
    p.write_text("keep = 1\n")

    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(fsio.os, "replace", boom)
    with pytest.raises(OSError, match="disk full"):
        fsio.atomic_write(p, "lost = 1\n")
    assert p.read_text() == "keep = 1\n"
    assert _leftovers(tmp_path) == []


def test_concurrent_writers_never_tear_the_file(tmp_path):
    p = tmp_path / "shared.toml"
    bodies = [f"writer = {i}\n" + "x" * 200_000 + "\n" for i in range(8)]
    fsio.atomic_write(p, bodies[0])
    seen: set[int] = set()
    stop = threading.Event()

    def read() -> None:
        while not stop.is_set():
            seen.add(len(p.read_text()))

    reader = threading.Thread(target=read)
    reader.start()
    writers = [
        threading.Thread(target=lambda b=b: [fsio.atomic_write(p, b) for _ in range(20)])
        for b in bodies
    ]
    for w in writers:
        w.start()
    for w in writers:
        w.join()
    stop.set()
    reader.join()
    assert seen <= {len(b) for b in bodies}, "a reader saw a partial file"
    assert _leftovers(tmp_path) == []


# -- the mode bug the old writer had (B8ed282605c) --------------------------------------


def test_the_config_writer_keeps_the_files_mode(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text("x = 1\n")
    p.chmod(0o664)
    tomlcfg.atomic_write(p, "x = 2\n")  # the old name, re-exported
    assert _mode(p) == 0o664


def test_ddflow_config_does_not_make_the_config_private(repo):
    assert run_cli(repo, "init")[0] == 0
    cfg = repo / ".ddflow" / "config.toml"
    cfg.chmod(0o664)
    assert run_cli(repo, "config", "schedule.parallel", "fixed")[0] == 0
    assert _mode(cfg) == 0o664


# -- file_lock -----------------------------------------------------------------------


def test_a_second_holder_times_out_while_the_first_holds(tmp_path):
    lock = tmp_path / ".x.lock"
    with fsio.file_lock(lock):
        t0 = time.monotonic()
        with pytest.raises(fsio.LockTimeout) as err, fsio.file_lock(lock, timeout_s=0.3):
            pass
        assert time.monotonic() - t0 >= 0.25
        assert str(lock) in str(err.value)
    with fsio.file_lock(lock, timeout_s=0):  # released: one try is enough
        pass


def test_a_waiter_without_timeout_gets_the_lock_when_it_is_released(tmp_path):
    lock = tmp_path / ".y.lock"
    order: list[str] = []
    held = threading.Event()

    def holder() -> None:
        with fsio.file_lock(lock):
            held.set()
            time.sleep(0.3)
            order.append("holder done")

    t = threading.Thread(target=holder)
    t.start()
    held.wait()
    with fsio.file_lock(lock):
        order.append("waiter in")
    t.join()
    assert order == ["holder done", "waiter in"]


def test_the_lock_is_released_by_an_exception(tmp_path):
    lock = tmp_path / ".z.lock"
    with pytest.raises(RuntimeError), fsio.file_lock(lock):
        raise RuntimeError("inside")
    with fsio.file_lock(lock, timeout_s=0):
        pass


def test_the_config_lock_is_the_dot_lock_beside_the_file(tmp_path):
    cfg = tmp_path / "gates.toml"
    assert fsio.lock_path_for(cfg) == tmp_path / ".gates.toml.lock"
    with tomlcfg.locked(cfg), pytest.raises(fsio.LockTimeout):
        with fsio.file_lock(fsio.lock_path_for(cfg), timeout_s=0):
            pass


def test_the_lock_excludes_another_process(tmp_path):
    lock = tmp_path / ".p.lock"
    r, w = os.pipe()
    pid = os.fork()
    if pid == 0:  # child: hold the lock until the parent has tried
        os.close(r)
        with fsio.file_lock(lock):
            os.write(w, b"x")
            time.sleep(0.5)
        os._exit(0)
    os.close(w)
    os.read(r, 1)
    try:
        with pytest.raises(fsio.LockTimeout), fsio.file_lock(lock, timeout_s=0.1):
            pass
    finally:
        os.waitpid(pid, 0)
        os.close(r)
    with fsio.file_lock(lock, timeout_s=0):
        pass


# -- core.digest ---------------------------------------------------------------------


def test_digest_matches_hashlib_and_cuts_to_length():
    assert digest("abc") == hashlib.sha256(b"abc").hexdigest()
    assert digest(b"abc", "sha1") == hashlib.sha1(b"abc").hexdigest()
    assert digest("abc", length=12) == hashlib.sha256(b"abc").hexdigest()[:12]
    assert digest("é") == hashlib.sha256("é".encode()).hexdigest()
    assert digest("\udcff", errors="surrogateescape") == hashlib.sha256(b"\xff").hexdigest()
