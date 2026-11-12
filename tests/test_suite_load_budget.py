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
    """The wait ends before the block starts, so a per-test clock begun inside the block starts
    at admission and the load wait never comes off it (DX-ci-timeout-excludes-queue)."""
    marks: dict[str, float] = {}
    with hold(cfg(), "first", tmp_path):

        def second():
            queued = time.monotonic()
            with hold(cfg(), "second", tmp_path, source=load(0.9, 0.9, 0.9, 0.1)):
                marks["admitted"] = time.monotonic()
                marks["queued"] = queued

        t = threading.Thread(target=second, daemon=True)
        t.start()
        t.join(10)
    assert marks["admitted"] - marks["queued"] >= 0.2  # it waited (poll is 0.25 s)
    # a clock started at admission has read none of that wait:
    assert time.monotonic() - marks["admitted"] < marks["admitted"] - marks["queued"]


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
    """The slot files are shared: a holder in slot 5 (another project allowed more slots) is
    seen by a project whose own cap is 1, which then neither admits into slot 0 nor skips the
    load check as 'the lone suite'."""
    big = cfg(cap=8)
    small = cfg(cap=1, wait=0.4)
    slots_held = []
    with hold(big, "a", tmp_path), hold(big, "b", tmp_path, source=load(0.0)):
        slots_held = SL._slot_indexes(tmp_path, 1)
        with pytest.raises(SL.SuiteBusy):
            with hold(small, "c", tmp_path, source=load(0.0)):
                pass
    assert len(slots_held) >= 2


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
    t.join(10)
    assert got == ["admitted"]
