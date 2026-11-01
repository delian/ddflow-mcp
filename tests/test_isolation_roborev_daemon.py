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
import subprocess
import time
from pathlib import Path

import _isolation as ISO
import pytest


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _spawn(home: Path) -> subprocess.Popen:
    env = dict(os.environ, HOME=str(home))
    return subprocess.Popen(
        ["roborev", "daemon", "run", "--addr", f"unix://{home / 'roborev.sock'}"],
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
    """The teardown that deletes the scratch ``HOME`` also kills the daemon the suite spawned
    there, so none is orphaned holding its address over the deleted database (B7d61f8c094)."""
    if shutil.which("roborev") is None:
        pytest.skip("roborev is not installed")
    home = tmp_path / "home"
    home.mkdir(mode=0o700)  # roborev refuses a group/world-accessible unix-socket dir
    proc = _spawn(home)
    try:
        pid = _pid_under(home, time.monotonic() + 15)
        assert pid is not None, "the daemon wrote no runtime pidfile under the scratch HOME"
        ISO._teardown(home)
        assert not home.exists(), "teardown removed the scratch HOME"
        proc.wait(timeout=10)  # reaped: not a live survivor holding the deleted DB
        assert not _alive(pid), "the daemon survived teardown; it would hold the deleted DB"
    finally:
        if home.exists():
            ISO.terminate_suite_daemons(home)
        if proc.poll() is None:
            proc.kill()
        proc.wait()


def test_terminate_leaves_a_home_with_no_daemon_alone(tmp_path: Path) -> None:
    """No pidfile under ``home`` means no daemon there: the teardown finds nothing to kill and
    the operator's real daemon (whose pidfile is under the real ``HOME``) is never touched."""
    home = tmp_path / "empty"
    home.mkdir()
    assert ISO.terminate_suite_daemons(home) == 0


def test_an_unidentifiable_pid_is_not_the_daemon() -> None:
    """A pid whose command line cannot be read (here: one pid ``pid_max`` can never name)
    is not PROVED roborev, so ``terminate_suite_daemons`` leaves it alone (B7d61f8c094)."""
    assert ISO._is_roborev_daemon(ISO._pid_max() + 1) is False
