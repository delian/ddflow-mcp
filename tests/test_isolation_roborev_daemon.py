"""The suite's scratch ``HOME`` must not orphan a roborev daemon (B7d61f8c094).

When the suite shells out to ``roborev`` the CLI starts a daemon that inherits the suite's
disposable ``HOME`` and keeps its SQLite ``reviews.db`` under it. ``tests/_isolation.py`` used
to delete that ``HOME`` on teardown without killing the daemon first, so the daemon survived the
teardown holding ``127.0.0.1:7373`` over a deleted database and every later roborev gate
repo-wide failed with ``no such table: repos/daemon_state``.
"""

from __future__ import annotations

import os
import shutil
import signal
import socket
import subprocess
import time
from pathlib import Path

import _isolation as ISO
import pytest


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _held(port: int) -> bool:
    """Whether an address is taken again: bind fails where it succeeds on a free port."""
    probe = socket.socket()
    probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        probe.bind(("127.0.0.1", port))
        probe.close()
        return False
    except OSError:
        return True


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _spawn(home: Path, port: int) -> subprocess.Popen:
    env = dict(os.environ, HOME=str(home))
    return subprocess.Popen(
        ["roborev", "daemon", "run", "--addr", f"127.0.0.1:{port}"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def _pid_under(home: Path, deadline: float) -> int | None:
    while time.monotonic() < deadline:
        for pidfile in sorted((home / ISO.ROBOREV_RUNTIME).glob("daemon.*.json")):
            if (pid := ISO._daemon_pid(pidfile)) is not None:
                return pid
        time.sleep(0.1)
    return None


def test_teardown_kills_a_daemon_the_suite_spawned_under_its_scratch_home(tmp_path: Path) -> None:
    if shutil.which("roborev") is None:
        pytest.skip("roborev is not installed")
    home = tmp_path / "home"
    home.mkdir()
    port = _free_port()
    proc = _spawn(home, port)
    try:
        pid = _pid_under(home, time.monotonic() + 15)
        assert pid is not None, "the daemon wrote no runtime pidfile under the scratch HOME"
        assert _alive(pid) and _held(port), "the daemon must be up and holding its address"
        assert ISO.terminate_suite_daemons(home) >= 1
        assert not _held(port), "the daemon's address is still taken after teardown"
        proc.wait(timeout=10)  # the daemon exited (SIGKILLed where it ignored SIGTERM)
        assert not _alive(pid), "the daemon survived teardown; it would hold the deleted DB"
    finally:
        pid = _pid_under(home, time.monotonic() + 1)
        for death in (signal.SIGTERM, signal.SIGKILL):
            if pid is not None and _alive(pid):
                try:
                    os.kill(pid, death)
                except OSError:
                    pass
                time.sleep(0.2)
        if proc.poll() is None:
            proc.kill()
        proc.wait()


def test_terminate_leaves_a_home_with_no_daemon_alone(tmp_path: Path) -> None:
    """No pidfile under ``home`` means no daemon there: the teardown finds nothing to kill and
    the operator's real daemon (whose pidfile is under the real ``HOME``) is never touched."""
    home = tmp_path / "empty"
    home.mkdir()
    assert ISO.terminate_suite_daemons(home) == 0
