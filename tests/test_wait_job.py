"""`wait --job`: a lane waiting on a detached review's run handle wakes when the run is over.

A detached review (`ddflow review --detach`) relaunches the review as a recorded job and
returns that job's id as the handle. Before this, the only way to know it had finished was a
bounded shell poll around `ddflow job list`. `wait --job <handle>` keeps the lane alive until
the job's process is over -- a detached review records its round before its process exits, so
"over" means "recorded" -- and returns `next_actions` naming what to do with the round.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from ddflow.api import lifecycle as A
from ddflow.config import Config
from ddflow.core import outcome as O
from ddflow.infra.log import EventLog
from ddflow.services import jobs as J


@pytest.fixture(autouse=True)
def _an_agent(monkeypatch):
    for var in ("DDFLOW_AGENT", "CLAUDECODE"):
        monkeypatch.delenv(var, raising=False)


def _log(repo: Path) -> EventLog:
    return EventLog(repo, "asker")


def _started(log: EventLog, item: str = "fix-X", *, job: str = "J1", host: str = ""):
    log.append(
        "job.started",
        job,
        {"item": item, "command": "python -m ddflow review fix-X", "pid": 1, "host": host or J.host(), "log": "jobs/j.log"},
    )


def test_a_job_wait_blocks_while_running_then_wakes_ready_with_next_actions(repo, monkeypatch):
    cfg = Config()
    log = _log(repo)
    _started(log)
    running = {"on": True}
    monkeypatch.setattr(
        J,
        "status",
        lambda job: J.Status("running", "pid 1 running")
        if running["on"]
        else J.Status("exited", "exited 0 (from its log)", 0),
    )

    # While it is running the wait refuses to guess, and names the handle it is waiting on.
    still = A.wait(repo, job="J1", timeout_s=0, agent="asker")
    assert still.exit == O.NOTHING, still.reason
    assert not still.data["woke"] and still.data["ready"] == []
    assert still.data["next_actions"] == [], "no round recorded yet: no next actions"

    # The detached review finishes while the lane waits: the wait wakes READY.
    running["on"] = False
    out = A.wait(repo, job="J1", timeout_s=20, poll_s=0.05, agent="asker")
    assert out.exit == O.OK, out.reason
    assert out.data["woke"] and out.data["ready"] == ["J1"]
    assert out.data["job"] == "J1"
    assert any("fix-X" in a for a in out.data["next_actions"]), out.data["next_actions"]


def test_a_job_killed_before_recording_its_round_is_hopeless_not_ready(repo, monkeypatch):
    _started(_log(repo))
    monkeypatch.setattr(J, "status", lambda job: J.Status("gone", "pid 1 is gone and its log records no exit: it was killed"))
    out = A.wait(repo, job="J1", timeout_s=5, poll_s=0.05, agent="asker")
    assert out.exit == O.NOTHING and "killed before its round was recorded" in out.reason
    assert out.data["next_actions"] == []


def test_an_unknown_job_is_hopeless(repo):
    out = A.wait(repo, job="J1", timeout_s=5, agent="asker")
    assert out.exit == O.NOTHING and "no such job" in out.reason


def test_a_job_on_another_host_is_hopeless(repo):
    _started(_log(repo), host="some-other-host")
    out = A.wait(repo, job="J1", timeout_s=5, poll_s=0.05, agent="asker")
    assert out.exit == O.NOTHING and "cannot be checked" in out.reason


def test_job_wait_refuses_an_item_with_it(repo):
    _started(_log(repo))
    out = A.wait(repo, job="J1", item="fix-X", timeout_s=5, agent="asker")
    assert out.exit == O.FAIL and "job is what to wait on" in out.reason
