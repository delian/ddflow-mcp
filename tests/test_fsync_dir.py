"""AU-durability: a rename or a freshly created file only survives a crash once its
parent DIRECTORY is fsynced. A file's own fsync covers its bytes, not its name: on ext4
and most journaled filesystems a rename (or a new file entry) is durable only after the
directory holding the name is fsynced, so a crash right after the rename can restore the
old name even though the file's contents were flushed.

These tests fail against the unfixed code: `atomic_write`, `atomic_file` and a newly
created event-log shard flush the file but never its directory.
"""

from __future__ import annotations

import os

import pytest

from ddflow.adapters import fsio
from ddflow.infra.log import EventLog

#: An event kind `EventLog.append` accepts, with a payload no redaction touches.
_EVENT = ("task.added", "A1", {"title": "t", "kind": "task"})


@pytest.fixture(autouse=True)
def _no_stamp(monkeypatch):
    monkeypatch.setattr(EventLog, "stamp", False)


def _record_dir_fsyncs(monkeypatch):
    """Patch `os.open`/`os.fsync` (the module `fsio` shares with the log) and record the
    file descriptors that a directory is opened with and then fsynced on."""
    real_open, real_fsync = os.open, os.fsync
    dir_fds: set[int] = set()
    synced: list[bool] = []

    def open_(path, flags, mode=0o777, *a, **k):
        fd = real_open(path, flags, mode, *a, **k)
        if os.path.isdir(path):
            dir_fds.add(fd)
        return fd

    def fsync_(fd):
        if fd in dir_fds:
            synced.append(True)
        real_fsync(fd)

    monkeypatch.setattr(fsio.os, "open", open_)
    monkeypatch.setattr(fsio.os, "fsync", fsync_)
    return synced


def test_atomic_write_fsyncs_the_parent_directory(tmp_path, monkeypatch):
    synced = _record_dir_fsyncs(monkeypatch)
    fsio.atomic_write(tmp_path / "sub" / "f.txt", "line\n")
    assert synced, "atomic_write never fsynced the directory after the rename"


def test_atomic_file_fsyncs_the_parent_directory(tmp_path, monkeypatch):
    synced = _record_dir_fsyncs(monkeypatch)
    with fsio.atomic_file(tmp_path / "sub" / "f.txt") as fh:
        fh.write(b"line\n")
    assert synced, "atomic_file never fsynced the directory after the rename"


def test_fsync_false_skips_the_directory_fsync(tmp_path, monkeypatch):
    synced = _record_dir_fsyncs(monkeypatch)
    fsio.atomic_write(tmp_path / "sub" / "f.txt", "line\n", fsync=False)
    assert synced == [], "fsync=False must also skip the directory fsync"


def test_a_new_shard_fsyncs_its_directory(tmp_path, monkeypatch):
    synced = _record_dir_fsyncs(monkeypatch)
    EventLog(tmp_path, "au").append(*_EVENT)
    assert synced, "a newly created shard never fsynced its directory"
