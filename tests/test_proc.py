"""infra.proc.kill_group: the signal is a choice (B-uni-proc.5-last-sites)."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time

import pytest

from ddflow.infra import proc as P

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="process groups are POSIX")


def _child() -> tuple[P.Popen, int]:
    """A shell in a session of its own (as `run_shell` starts one) and the pid of the
    grandchild it started."""
    p = P.popen(
        ["sh", "-c", "sleep 60 & echo $!; wait"],
        start_new_session=True,
        stdout=P.PIPE,
        text=True,
    )
    assert p.stdout is not None
    return p, int(p.stdout.readline())


def _gone(pid: int, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.02)
    return False


@pytest.mark.parametrize(
    ("sig", "code"), [(None, -signal.SIGKILL), (signal.SIGTERM, -signal.SIGTERM)]
)
def test_kill_group_sends_the_chosen_signal_to_the_whole_group(sig, code):
    """Mutants: the signal ignored (always SIGKILL); the direct child signalled alone."""
    p, grandchild = _child()
    P.kill_group(p, sig)
    assert p.wait(timeout=10) == code
    assert _gone(grandchild), "the grandchild was left running"


def test_the_popen_alias_is_the_standard_one():
    assert P.Popen is subprocess.Popen


@pytest.mark.parametrize("sig", [None, signal.SIGTERM])
@pytest.mark.parametrize("error", [ProcessLookupError, PermissionError])
def test_a_group_that_cannot_be_signalled_is_not_an_error(monkeypatch, sig, error):
    """Mutant: `kill_group` letting the error out. The group is gone, or not ours: the direct
    child is all that is left to reach. The signal is faked, never sent to a pid that
    may have been recycled."""
    p = P.popen(["sleep", "60"], start_new_session=True)
    try:

        def refuse(pgid, signum):
            raise error

        monkeypatch.setattr(os, "killpg", refuse)
        P.kill_group(p, sig)
        assert p.wait(timeout=10) == -(sig or signal.SIGKILL)
    finally:
        p.kill()
        p.wait()
