"""The `Platform` port: the same cases against `FakePlatform` and the real `Posix`
adapter. `CONFORMANCE` is read by tests/test_ports_contract.py.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from ddflow.adapters import platform as P
from ddflow.adapters.fakes.platform import FakePlatform


def platform_cases(io) -> None:
    assert io.which("this-tool-does-not-exist-ddflow") is None
    assert isinstance(io.home_dir(), Path)
    assert io.is_terminal(object()) is False  # no isatty -> not a terminal


CONFORMANCE = {
    "Platform": {
        "real": lambda monkeypatch: P.Posix(),
        "cases": platform_cases,
        "fake": lambda cls: cls(),
    }
}


def test_posix_chmod_private_is_owner_only(tmp_path):
    f = tmp_path / "secret"
    f.write_text("x")
    P.DEFAULT.chmod_private(f)
    assert os.stat(f).st_mode & 0o777 == 0o600


def test_posix_file_lock_holds_an_advisory_lock(tmp_path):
    lock = tmp_path / "held.lock"
    with P.DEFAULT.file_lock(lock):
        assert lock.exists()


def test_the_windows_adapter_reports_unavailable():
    win = P.Windows()
    with pytest.raises(NotImplementedError):
        win.home_dir()
    with pytest.raises(NotImplementedError):
        win.which("git")


def test_the_fake_records_and_answers():
    io = FakePlatform(which_of={"git": "/usr/bin/git"})
    assert io.which("git") == "/usr/bin/git"
    assert io.which("nope") is None
    assert io.calls[0] == ("which", ("git",))
