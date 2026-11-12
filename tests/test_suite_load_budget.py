"""The suite lock admits by load and by a core-derived cap (SL-suite-load-budget)."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from ddflow.config import Config
from ddflow.infra.signals import FakeSource
from ddflow.services import suitelock as SL


def cfg(*, ceiling=0.35, cap=0, per_suite=96, ci_slots=1, wait=10.0, poll=0.0):
    return NS(
        ci=NS(
            max_concurrent_suites=ci_slots,
            suite_wait_s=wait,
            suite_queue_stale_s=120,
            suite_queue_grace_s=120,
        ),
        suites=NS(
            max_concurrent=cap,
            cores_per_suite=per_suite,
            load_ceiling_per_core=ceiling,
            load_poll_s=poll,
        ),
    )


def _until(pred, timeout=10.0):
    end = time.monotonic() + timeout
    while not pred():
        assert time.monotonic() < end, "timed out"
        time.sleep(0.02)


def load(*values):
    return FakeSource([{"load": v} for v in values])


def hold(c, name, d, source=None, say=None, **kw):
    return SL.hold(c, name, d, say=say or (lambda _t: None), source=source, cores=192, **kw)


def test_the_cap_is_derived_from_cores_unless_set():
    assert SL.limit(cfg(), cores=192) == 2
    assert SL.limit(cfg(), cores=64) == 1
    assert SL.limit(cfg(cap=3), cores=8) == 3
    assert SL.limit(cfg(ci_slots=4), cores=64) == 4  # [ci].max_concurrent_suites stays a floor


def test_a_cfg_without_the_section_keeps_the_ci_limit():
    old = NS(ci=NS(max_concurrent_suites=3))
    assert SL.limit(old, cores=192) == 3


def test_the_knobs_exist_with_documented_defaults():
    s = Config().suites
    assert s.max_concurrent == 0
    assert s.load_ceiling_per_core > 0 and s.cores_per_suite >= 1 and s.load_poll_s >= 0


def test_a_second_suite_waits_when_the_load_is_over_the_ceiling_and_says_why(tmp_path: Path):
    said: list[str] = []
    with hold(cfg(), "first", tmp_path):
        with pytest.raises(SL.SuiteBusy) as e:
            with hold(cfg(wait=0.5), "second", tmp_path, source=load(0.8), say=said.append):
                pass
    text = str(e.value)
    assert "load" in text and "0.80" in text and "0.35" in text
    assert any("load 0.80" in line for line in said)


def test_a_second_suite_is_admitted_when_the_load_is_under_the_ceiling(tmp_path: Path):
    got: list[str] = []
    with hold(cfg(), "first", tmp_path):
        with hold(cfg(), "second", tmp_path, source=load(0.1)):
            got.append("second")
    assert got == ["second"]


def test_a_lone_suite_is_never_held_back_by_load(tmp_path: Path):
    # Nothing else is running: whatever the load is, it is not from another suite.
    with hold(cfg(), "only", tmp_path, source=load(0.99)):
        pass


def test_the_second_suite_starts_once_the_load_falls(tmp_path: Path):
    order: list[str] = []

    def second():
        with hold(cfg(), "second", tmp_path, source=load(0.9, 0.9, 0.2)):
            order.append("second")

    with hold(cfg(), "first", tmp_path):
        t = threading.Thread(target=second, daemon=True)
        t.start()
        time.sleep(0.3)
        assert order == []  # held back while the load was over the ceiling
        t.join(10)
    assert order == ["second"]


def test_an_unreadable_load_does_not_block(tmp_path: Path):
    with hold(cfg(), "first", tmp_path):
        with hold(cfg(), "second", tmp_path, source=FakeSource([{}])):
            pass


def test_the_cap_still_bounds_the_suites_whatever_the_load(tmp_path: Path):
    with hold(cfg(cap=1), "first", tmp_path):
        with pytest.raises(SL.SuiteBusy):
            with hold(cfg(cap=1, wait=0.4), "second", tmp_path, source=load(0.0)):
                pass


def test_waiting_for_load_is_queue_time_not_run_time(tmp_path: Path):
    """The wait ends before the block starts: a clock begun on entering the block (the per-test
    clock, DX-ci-timeout-excludes-queue) has read none of the load wait."""
    marks: dict[str, float] = {}
    with hold(cfg(), "first", tmp_path):

        def second():
            queued = time.monotonic()
            with hold(cfg(), "second", tmp_path, source=load(0.9, 0.9, 0.9, 0.1)):
                begun = time.monotonic()  # the per-test clock starts here, at admission
                time.sleep(0.01)
                marks["clock"] = time.monotonic() - begun
            marks["queue_wait"] = begun - queued

        t = threading.Thread(target=second, daemon=True)
        t.start()
        t.join(10)
    assert marks["queue_wait"] >= 0.5  # three polls of 0.25 s with the load over the ceiling
    assert marks["clock"] < marks["queue_wait"]  # the wait is not on the run clock


def test_a_poll_interval_never_lets_a_waiter_skip_the_load_check_on_reaching_the_head(
    tmp_path: Path,
):
    """Behind another waiter the load is not read; on reaching the head it is read at once, not
    after the poll interval, so the check cannot be skipped by a stale 'no reason'."""
    c = cfg(poll=3600.0, wait=0.6)
    with hold(c, "first", tmp_path):
        with pytest.raises(SL.SuiteBusy) as e:
            with hold(c, "second", tmp_path, source=load(0.9)):
                pass
    assert "load" in str(e.value)


def test_a_neighbour_with_a_bigger_cap_counts_toward_this_projects_cap_and_load(tmp_path: Path):
    """The slot files are shared: a holder in slot 5 (another project allowed more slots) is seen
    by a project whose own cap is 1, which then neither admits into free slot 0 nor skips the
    load check as 'the lone suite'."""
    from ddflow.infra import fsio
    from ddflow.services import slots as SLOTS

    held = SLOTS.Slots(tmp_path, SL.NAME).path(5)
    with fsio.file_lock(held, 0):
        assert SL._held(tmp_path, 5) and not SL._held(tmp_path, 0)
        with pytest.raises(SL.SuiteBusy):
            with hold(cfg(cap=1, wait=0.4), "agent", tmp_path, source=load(0.0)):
                pass
        # and the load check applies: with room under a bigger cap it is the load that holds
        with pytest.raises(SL.SuiteBusy) as e:
            with hold(cfg(cap=8, wait=0.4), "agent", tmp_path, source=load(0.9)):
                pass
        assert "load" in str(e.value)


def test_a_slot_freed_while_the_load_is_over_the_ceiling_is_not_taken(tmp_path: Path):
    """Every slot is held at the first look, then one frees while the load is over the ceiling:
    the waiter reads the load afresh (not a stale 'no reason') and stays out."""
    c = cfg(cap=2, poll=3600.0, wait=1.5)
    outcome: list[str] = []

    src = load(0.1, 0.9)

    def waiter():
        try:
            with hold(c, "d", tmp_path, source=src):
                outcome.append("admitted")
        except SL.SuiteBusy as e:
            outcome.append(str(e))

    with hold(c, "a", tmp_path):
        with hold(c, "b", tmp_path, source=load(0.0)):
            t = threading.Thread(target=waiter, daemon=True)
            t.start()
            _until(lambda: src.calls >= 1)  # d has looked: both slots are held
        t.join(10)  # b is gone; the load reads 0.9 from now on
    assert outcome and "load" in outcome[0], outcome


def test_the_poll_knob_is_bounded():
    from ddflow.config_sections.suites import _poll_seconds

    assert _poll_seconds(2.0) == "" and _poll_seconds(0) == ""
    assert _poll_seconds(1e308) and _poll_seconds(float("inf")) and _poll_seconds(-1)


def test_a_nan_or_infinite_load_is_unreadable_and_never_blocks(tmp_path: Path):
    for bad in (float("nan"), float("inf")):
        with hold(cfg(), "first", tmp_path):
            with hold(cfg(), "second", tmp_path, source=load(bad)):
                pass


def test_a_holder_that_leaves_during_a_cached_block_lets_the_waiter_look_again(tmp_path: Path):
    """The load reading is remembered for the poll interval, the holders are counted at every
    look: once the only other suite is gone the waiter is admitted at the next poll, not after
    the interval (a lone suite is never held back by load)."""
    c = cfg(cap=2, poll=3600.0, wait=10.0)
    got: list[str] = []
    src = load(0.9)

    def waiter():
        with hold(c, "d", tmp_path, source=src):
            got.append("admitted")

    with hold(c, "a", tmp_path):
        t = threading.Thread(target=waiter, daemon=True)
        t.start()
        _until(lambda: src.calls >= 1)  # blocked by the load, reading cached for an hour
        time.sleep(0.6)  # several polls later it is still held back
        assert got == []
    t.join(10)
    assert got == ["admitted"]


def test_the_ci_run_budget_starts_at_admission_after_a_load_wait(tmp_path: Path, monkeypatch):
    """Through the production path: a run held back by the load budget gets its WHOLE timeout
    budget once admitted, and the seconds it reports are the run's own, not the queue's
    (B4f35548c0a, DX-ci-timeout-excludes-queue)."""
    from ddflow.services import ci as CI
    from ddflow.services.ci import runner

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    seen: dict[str, float] = {}

    def execute(cfg, tree, cmd, scope, budget, tick=None, holding_suite=False, used=None):
        seen["budget"] = budget
        began = time.monotonic()
        time.sleep(0.01)
        return NS(kind="", ran=True), "", time.monotonic() - began

    monkeypatch.setattr(runner, "_execute", execute)
    src = load(0.9, 0.9, 0.9, 0.1)
    monkeypatch.setattr(SL.SIG, "HostSignals", lambda *a, **k: src)
    c = Config()
    c.suites.max_concurrent = 2
    c.suites.load_poll_s = 0.0
    out: dict = {}

    def second():
        started = time.monotonic()
        _p, _prefix, waited, elapsed = CI._run_locked(
            c, "second", None, "", None, 7200, None, {}, True
        )
        out.update(waited=waited, elapsed=elapsed, total=time.monotonic() - started)

    with SL.hold(c, "first", tmp_path / "state" / "ddflow" / "suites", say=lambda _t: None):
        t = threading.Thread(target=second, daemon=True)
        t.start()
        t.join(15)
    assert seen["budget"] == 7200  # the whole timeout, whatever the wait
    assert out["waited"] >= 0.4  # it queued for the load (three polls of 0.25 s)
    assert out["elapsed"] < out["waited"]  # the run's own seconds exclude the queue
