"""infra.proc.kill_group: the signal is a choice (B-uni-proc.5-last-sites)."""

from __future__ import annotations

import signal
import sys

import pytest

from ddflow.infra import proc as P

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="process groups are POSIX")


def _child() -> P.Popen:
    # A shell that starts a grandchild, in a session of its own as `run_shell` does.
    return P.popen(["sh", "-c", "sleep 60 & wait"], start_new_session=True)


@pytest.mark.parametrize(
    ("sig", "code"), [(None, -signal.SIGKILL), (signal.SIGTERM, -signal.SIGTERM)]
)
def test_kill_group_sends_the_chosen_signal_to_the_whole_group(sig, code):
    """Mutant: the signal argument ignored (always SIGKILL)."""
    p = _child()
    if sig is None:
        P.kill_group(p)
    else:
        P.kill_group(p, sig)
    assert p.wait(timeout=10) == code


def test_the_popen_alias_is_the_standard_one():
    import subprocess

    assert P.Popen is subprocess.Popen
