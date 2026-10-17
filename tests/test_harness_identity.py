"""`harness_identity.liveness`: is the process that declared an agent still running?"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ddflow.infra import harness_identity as HI


def _record(repo: Path, pid: int, agent: str) -> None:
    d = HI._dir(repo)
    d.mkdir(exist_ok=True)
    (d / f"{pid}-{HI._stat(pid)[2]}").write_text(agent + "\n")


def test_no_record_means_unknown_not_dead(repo):
    assert HI.liveness(repo, "nobody") == (None, [])


def test_a_running_harness_is_alive_and_an_exited_one_is_gone(repo):
    p = subprocess.Popen(["sleep", "60"])
    try:
        _record(repo, p.pid, "agent-x")
        assert HI.liveness(repo, "agent-x") == (True, [p.pid])
        assert HI.liveness(repo, "agent-y") == (None, [])  # another agent: not this record
    finally:
        p.kill()
        p.wait()
    ok, pids = HI.liveness(repo, "agent-x")
    assert ok is False and pids == [p.pid]
