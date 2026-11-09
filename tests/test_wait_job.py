"""`wait --job`: a lane waiting on a detached review's run handle wakes when the round is recorded.

A detached review (`ddflow review --detach`) relaunches the review as a recorded job and
returns that job's id as the handle. Before this, the only way to know it had finished was a
bounded shell poll around `ddflow job list`. `wait --job <handle>` keeps the lane alive until
the job is over AND its review round is recorded, then returns `next_actions`. A job that is
over but recorded no round (killed or crashed) is refused up front, not woken READY.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from ddflow.api import lifecycle as A
from ddflow.api.lifecycle import wait_approval as WA
from ddflow.config import Config
from ddflow.core import outcome as O
from ddflow.core.model import Job, State
from ddflow.core.records.gates import GateRecord
from ddflow.core.records.items import Item
from ddflow.infra.log import EventLog
from ddflow.services import jobs as J


@pytest.fixture(autouse=True)
def _an_agent(monkeypatch):
    for var in ("DDFLOW_AGENT", "CLAUDECODE"):
        monkeypatch.delenv(var, raising=False)


def _log(repo: Path) -> EventLog:
    return EventLog(repo, "asker")


def _state(round_at: str | None):
    """A state with the reviewed item present (and, when `round_at` is given, a recorded
    review round at that time) and a job launched at a fixed earlier time."""
    st = State()
    gates = {"critic": GateRecord(gate="critic", outcome="passed", at=round_at)} if round_at else {}
    st.items["fix-X"] = Item(id="fix-X", kind="task", gates=gates)
    st.jobs["J1"] = Job(
        id="J1", item="fix-X", command="python -m ddflow review fix-X", pid=1,
        host=J.host(), log="jobs/j.log", started_at="2026-01-01T00:00:00Z",
    )
    return st


def test_a_job_wait_blocks_while_running_then_wakes_ready_with_next_actions(repo, monkeypatch):
    cfg = Config()
    log = _log(repo)
    log.append(
        "job.started", "J1",
        {"item": "fix-X", "command": "python -m ddflow review fix-X", "pid": 1, "host": J.host(), "log": "jobs/j.log"},
    )
    running = {"on": True}
    monkeypatch.setattr(
        J,
        "status",
        lambda job: J.Status("running", "pid 1 running")
        if running["on"]
        else J.Status("exited", "exited 0 (from its log)", 0),
    )

    # While it is running the wait refuses to guess, and offers no next actions.
    still = A.wait(repo, job="J1", timeout_s=0, agent="asker")
    assert still.exit == O.NOTHING, still.reason
    assert not still.data["woke"] and still.data["ready"] == []
    assert still.data["next_actions"] == [], "no round recorded yet: no next actions"

    # The detached review finishes (and, with no item in the log, "over" IS the round):
    # the wait wakes READY.
    running["on"] = False
    out = A.wait(repo, job="J1", timeout_s=20, poll_s=0.05, agent="asker")
    assert out.exit == O.OK, out.reason
    assert out.data["woke"] and out.data["ready"] == ["J1"]
    assert out.data["job"] == "J1"
    assert any("fix-X" in a for a in out.data["next_actions"]), out.data["next_actions"]


def test_a_job_over_with_a_recorded_round_is_ready(monkeypatch):
    monkeypatch.setattr(J, "status", lambda job: J.Status("exited", "exited 0 (from its log)", 0))
    v = WA._judge_job(_state("2026-01-02T00:00:00Z"), "J1", 0.0)
    assert v["status"] == "ready", v["why"]


def test_a_job_over_with_NO_recorded_round_is_hopeless_not_ready(monkeypatch):
    # The job is over (killed or crashed before recording) but the item has no round:
    # waiting cannot record one, and waking READY would promise a round that is not there.
    monkeypatch.setattr(J, "status", lambda job: J.Status("gone", "pid 1 is gone and its log records no exit"))
    v = WA._judge_job(_state(None), "J1", 0.0)
    assert v["status"] == "hopeless", v["why"]
    assert "recorded no review round" in v["why"]


def test_an_old_round_from_before_the_job_does_not_count(monkeypatch):
    monkeypatch.setattr(J, "status", lambda job: J.Status("exited", "exited 0 (from its log)", 0))
    # A round recorded BEFORE the job launched is not this detached review's round.
    v = WA._judge_job(_state("2020-01-01T00:00:00Z"), "J1", 0.0)
    assert v["status"] == "hopeless", v["why"]


def test_an_unknown_job_is_hopeless(repo):
    out = A.wait(repo, job="J1", timeout_s=5, agent="asker")
    assert out.exit == O.NOTHING and "no such job" in out.reason


def test_a_job_on_another_host_is_hopeless(repo):
    st = State()
    st.jobs["J1"] = Job(id="J1", item="", pid=0, host="some-other-host", started_at="2026-01-01")
    log = _log(repo)
    log.append("job.started", "J1", {"item": "", "command": "x", "pid": 0, "host": "some-other-host", "log": ""})
    out = A.wait(repo, job="J1", timeout_s=5, poll_s=0.05, agent="asker")
    assert out.exit == O.NOTHING and "cannot be checked" in out.reason


def test_job_wait_refuses_an_item_with_it(repo):
    _log(repo).append("job.started", "J1", {"item": "fix-X", "command": "x", "pid": 1, "host": J.host(), "log": ""})
    out = A.wait(repo, job="J1", item="fix-X", timeout_s=5, agent="asker")
    assert out.exit == O.FAIL and "job is what to wait on" in out.reason
