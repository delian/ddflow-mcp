# ruff: noqa: F811  (fixtures imported from test_release_at_enqueue)
"""Something runs the integrator (LC-integrator-trigger): accepting a release starts one
detached run through the job machinery, a tick restarts a dead one, `auto_run = off` starts
nothing, and a wait with nobody integrating is a visible warning that names the next call."""

from __future__ import annotations

import subprocess
import sys
from types import SimpleNamespace

import pytest
from test_merge_queue import _finish, _git, proj  # noqa: F401
from test_release_at_enqueue import _release, rp  # noqa: F401

from ddflow.api import mergequeue_add as MA
from ddflow.api import mergequeue_start as MS
from ddflow.api._base import _load
from ddflow.api.lifecycle.brief import _queue_block
from ddflow.core import outcome as O
from ddflow.services import jobs as J
from ddflow.services import lanerelease as LR
from ddflow.services import mergequeue as MQ
from ddflow.services import ticks as TK


@pytest.fixture
def procs(monkeypatch):
    """The job launcher replaced: a 'launch' is a sleeping process of ours."""
    started: list[subprocess.Popen] = []

    def fake_launch(command, cwd, log):
        p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        started.append(p)
        return p.pid

    monkeypatch.setattr(J, "launch", fake_launch)
    yield started
    for p in started:
        p.kill()
        p.wait()


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setenv("DDFLOW_MERGE_QUEUE_AUTO_RUN", "on")


def _queue_add(rp, item):
    return MA.queue_add(rp, item, release=True, agent=item.lower())


def test_a_release_starts_exactly_one_integrator_job(rp, on, procs):
    _finish(rp, "T1")
    out = _queue_add(rp, "T1")
    assert out.exit == O.OK and out.data["released"] is True, out
    run = out.data["integrator_run"]
    assert run["started"] is True and run["job"] and run["log"], run
    assert len(procs) == 1
    _l, _c, st = _load(rp, "")
    assert [j.id for j in LR.live_jobs(rp, st)] == [run["job"]]
    assert "Integrator started as job" in out.data["text"]


def test_a_second_release_while_one_runs_starts_none(rp, on, procs):
    _finish(rp, "T1")
    _finish(rp, "T2")
    assert _queue_add(rp, "T1").data["integrator_run"]["started"] is True
    second = _queue_add(rp, "T2").data["integrator_run"]
    assert second["started"] is False and "already running" in second["reason"], second
    assert len(procs) == 1


def test_a_dead_integrator_job_is_restarted_by_the_tick(rp, on, procs, monkeypatch):
    _finish(rp, "T1")
    assert _queue_add(rp, "T1").data["integrator_run"]["started"] is True
    procs[0].kill()
    procs[0].wait()
    _l, cfg, st = _load(rp, "")
    entry = MQ.live(rp)[0]
    clock = SimpleNamespace(time=lambda: entry.queued_at + cfg.merge_queue.auto_run_after_s + 1)
    monkeypatch.setattr(MS, "time", clock)
    ctx = TK.TickCtx(repo=rp, cfg=cfg, state=st)
    note = MS.integrator_tick(ctx)
    assert note and note.startswith("started integrator job"), note
    assert len(procs) == 2


def test_the_tick_waits_until_the_entry_is_overdue(rp, on, procs, monkeypatch):
    _finish(rp, "T1")
    run_cli_add = MQ.add(rp, "T1", agent="t1", keep_history=50)  # queued, nobody integrating
    assert run_cli_add[1]
    _l, cfg, st = _load(rp, "")
    ctx = TK.TickCtx(repo=rp, cfg=cfg, state=st)
    monkeypatch.setattr(MS, "time", SimpleNamespace(time=lambda: MQ.live(rp)[0].queued_at + 1))
    assert MS.integrator_tick(ctx) is None and not procs


def test_auto_run_off_starts_nothing_and_says_how_to_run_it(rp, procs, monkeypatch):
    monkeypatch.setenv("DDFLOW_MERGE_QUEUE_AUTO_RUN", "off")
    _finish(rp, "T1")
    out = _queue_add(rp, "T1")
    run = out.data["integrator_run"]
    assert run["started"] is False and "auto_run = off" in run["reason"], run
    assert "ddflow queue run" in run["reason"] and "ddflow_queue" in run["reason"]
    assert not procs


def test_the_tick_is_registered_and_follows_the_knob(rp, on):
    names = {t.name: t for t in TK.registered()}
    assert "merge_queue.integrator" in names
    _l, cfg, st = _load(rp, "")
    ctx = TK.TickCtx(repo=rp, cfg=cfg, state=st)
    assert names["merge_queue.integrator"].enabled(ctx) is True


def test_a_wait_with_nobody_integrating_is_a_warning_with_the_next_call(rp, monkeypatch):
    monkeypatch.setenv("DDFLOW_MERGE_QUEUE_AUTO_RUN", "off")
    _finish(rp, "T1")
    assert _queue_add(rp, "T1").exit == O.OK
    _l, cfg, st = _load(rp, "")
    assert LR.warning(rp, cfg, st, now=MQ.live(rp)[0].queued_at) == "", "not overdue yet"
    late = MQ.live(rp)[0].queued_at + cfg.merge_queue.auto_run_after_s + 1
    text = LR.warning(rp, cfg, st, now=late)
    assert "1 entries waiting (1 released: T1), integrator not running" in text
    assert "`ddflow queue run`" in text and "`ddflow_queue action=run`" in text
    status = MA.queue_status(rp)
    assert "warning" in status.data
    assert _queue_block(rp, st, cfg) == "" or "integrator not running" in _queue_block(rp, st, cfg)


def test_a_parked_released_entry_is_named_with_the_call_that_resumes_it(rp):
    _finish(rp, "T1", "BAD\n")
    assert _queue_add(rp, "T1").exit == O.OK
    from ddflow.api.mergequeue_run import queue_run

    assert queue_run(rp, agent="integrator").exit == O.FAIL
    _l, cfg, st = _load(rp, "")
    text = LR.warning(rp, cfg, st)
    assert "T1 was released and is PARKED" in text and "ddflow claim T1" in text
    assert "ddflow_claim id=T1" in text


def test_the_start_is_single_flight_across_lock_holders(rp, on, procs):
    _finish(rp, "T1")
    MQ.add(rp, "T1", agent="t1", keep_history=50)
    got = [MS.start_integrator(rp) for _ in range(3)]
    assert [g["started"] for g in got] == [True, False, False], got
    assert len(procs) == 1
