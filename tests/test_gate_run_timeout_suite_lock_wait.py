"""Ba5d5d63f12: `gate run ... ci --timeout N` replaced the ci command's budget but not the
host suite-lock wait (`[ci].suite_wait_s`, 1800 s), so a queued run still gave up UNAVAILABLE
at the knob although the per-run override had authorised a much longer run. With an override
the override is the run's wait limit, replacing the knob in either direction: a longer one
extends the queue wait, a shorter one bounds it. The give-up still parks the ticket, so a
retry keeps its queue place."""

from __future__ import annotations

import contextlib
import time
from types import SimpleNamespace as NS

import pytest

from ddflow.config import Config
from ddflow.services import ci as CI
from ddflow.services import suitelock as SL
from ddflow.services.ci import runner


def test_a_wait_override_extends_the_suite_wait_knob(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    cfg = Config()
    cfg.ci.suite_wait_s = 0.2
    with SL.hold(cfg, "holder", tmp_path, say=lambda _t: None):
        started = time.monotonic()
        with pytest.raises(SL.SuiteBusy) as busy:
            # the per-run override (a `--timeout`) asks to wait far longer than the knob
            with SL.hold(cfg, "second", tmp_path, wait_s=1.2, say=lambda _t: None):
                raise AssertionError("the holder never freed the slot")
    waited = time.monotonic() - started
    assert waited >= 1.2, f"gave up after {waited:.2f}s, not the overridden wait of 1.2s"
    assert busy.value.waited_s >= 1.2
    assert busy.value.position == 1  # it queued behind the holder, it did not give up at once


def test_a_budget_override_bounds_the_lock_wait_in_both_directions():
    assert CI._lock_wait(7200) == 7200.0  # a longer override extends the knob
    assert CI._lock_wait(100) == 100.0  # a shorter override bounds the queued phase too
    assert CI._lock_wait(0) is None  # no override: the knob alone decides


def test_run_locked_hands_the_extended_wait_to_the_lock(monkeypatch):
    seen: dict[str, float | None] = {}

    @contextlib.contextmanager
    def fake_hold(cfg, holder, directory=None, *, say=None, wait_s=None):
        seen["wait_s"] = wait_s
        yield

    monkeypatch.setattr(
        CI.SL,
        "hold",
        fake_hold,
    )
    monkeypatch.setattr(
        runner,
        "_execute",
        lambda cfg=None, tree=None, cmd=None, scope=None, budget=None, tick=None, holding_suite=False, used=None: (
            NS(kind="", ran=True),
            "",
        ),
    )
    CI._run_locked(
        Config(), CI._holder(None, "HEAD"), None, "", None, 7200, None, {}, True, lock_wait=7200.0
    )
    assert seen == {"wait_s": 7200.0}, seen


def test_a_short_override_bounds_the_knob_down_through_the_lock(tmp_path, monkeypatch):
    """The DOWN direction of the override runs end to end, not only in `_lock_wait` alone."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    cfg = Config()
    cfg.ci.suite_wait_s = 1.2
    now = [0.0]

    def monotonic() -> float:  # the determinism of the poll loop comes with a fake clock:
        return now[0]  # no wall wait to race a loaded host in (the raise provokes no timer)

    def sleep(s: float) -> None:
        now[0] += s

    monkeypatch.setattr(time, "monotonic", monotonic)
    monkeypatch.setattr(time, "sleep", sleep)
    with SL.hold(cfg, "holder", tmp_path, say=lambda _t: None):
        with pytest.raises(SL.SuiteBusy) as busy:
            with SL.hold(cfg, "second", tmp_path, wait_s=0.6, say=lambda _t: None):
                raise AssertionError("the holder never freed the slot")
    assert busy.value.waited_s >= 0.6
    assert busy.value.waited_s < 1.1, (
        f"gave up after {busy.value.waited_s:.2f}s, at the knob (1.2s), not the override (0.6s)"
    )


def test_the_give_up_after_the_extended_wait_parks_the_ticket(tmp_path, monkeypatch):
    """The longer wait changed when the run gives up, not what the give-up does with its place."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    cfg = Config()
    cfg.ci.suite_wait_s = 0.2
    queue = tmp_path / "queue"
    with SL.hold(cfg, "holder", tmp_path, say=lambda _t: None):
        with pytest.raises(SL.SuiteBusy):
            with SL.hold(cfg, "second", tmp_path, wait_s=0.6, say=lambda _t: None):
                raise AssertionError("the holder never freed the slot")
        assert list(queue.glob("*-second.parked")), "the give-up did not park the ticket"


def test_a_waiter_that_reaches_the_head_restarts_its_wait_budget(tmp_path, monkeypatch):
    """B-suite-wait-expires-at-head-of-queue: `[ci].suite_wait_s` counts time IN LINE, not
    progress. A waiter that spent time in line behind others and THEN reaches the head must
    not be bounced the instant it arrives: its give-up budget runs fresh from reaching the
    head, so the line time does not eat it (a real run advanced #7 -> #1 and was bounced the
    moment it got there, recorded UNAVAILABLE with no test run)."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    cfg = Config()
    cfg.ci.suite_wait_s = 1.0
    now = [0.0]

    def monotonic() -> float:
        return now[0]

    def sleep(s: float) -> None:
        now[0] += s

    monkeypatch.setattr(time, "monotonic", monotonic)
    monkeypatch.setattr(time, "sleep", sleep)
    real_live = SL._live

    def fake_live(queue, stale_s, grace_s, mine=""):
        # OUR waiter sees a fake "ahead" in front of it for the first 0.75 s (position 2) and
        # is only then at the head; the holder's own ticket is left alone, so the holder takes
        # the slot at once and stays in it for the whole of "me"'s wait.
        if "-me" in mine and now[0] < 0.75:
            return ["ahead", mine]
        return real_live(queue, stale_s, grace_s, mine)

    monkeypatch.setattr(SL, "_live", fake_live)

    with SL.hold(cfg, "holder", tmp_path, say=lambda _t: None):
        with pytest.raises(SL.SuiteBusy) as busy:
            with SL.hold(cfg, "me", tmp_path, say=lambda _t: None):
                raise AssertionError("the holder never freed the slot")
    # It gave up at the head AFTER its full head budget (1.0 s) elapsed from reaching the head
    # at 0.75 s (0.75 line + 1.0 head), which only holds when reaching the head RESTARTED the
    # budget. Unfixed, the budget ran from queueing and it gave up at 1.0 s (line time alone).
    assert busy.value.position == 1, f"gave up at position {busy.value.position}, not the head"
    assert busy.value.waited_s >= 1.7, (
        f"gave up after {busy.value.waited_s:.2f}s: reaching the head did not restart the budget"
    )


def test_an_override_waiter_reaching_the_head_keeps_a_hard_total_bound(tmp_path, monkeypatch):
    """Ba5d5d63f12 holds across the head restart: an explicit `wait_s` override is a hard
    TOTAL bound, so a waiter that reaches the head late still gives up once the override
    elapsed from queueing -- the B-suite-wait-expires-at-head-of-queue restart applies to the
    default knob only."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    cfg = Config()
    cfg.ci.suite_wait_s = 5.0
    now = [0.0]

    def monotonic() -> float:
        return now[0]

    def sleep(s: float) -> None:
        now[0] += s

    monkeypatch.setattr(time, "monotonic", monotonic)
    monkeypatch.setattr(time, "sleep", sleep)
    real_live = SL._live

    def fake_live(queue, stale_s, grace_s, mine=""):
        if "-me" in mine and now[0] < 0.75:
            return ["ahead", mine]
        return real_live(queue, stale_s, grace_s, mine)

    monkeypatch.setattr(SL, "_live", fake_live)

    with SL.hold(cfg, "holder", tmp_path, say=lambda _t: None):
        with pytest.raises(SL.SuiteBusy) as busy:
            with SL.hold(cfg, "me", tmp_path, wait_s=1.0, say=lambda _t: None):
                raise AssertionError("the holder never freed the slot")
    # The 1.0 s override bounds the whole queue wait even though the waiter reached the head
    # at ~0.75 s: it gave up at ~1.0 s total, not at 0.75 + a restarted 1.0 = 1.75 s.
    assert 0.9 <= busy.value.waited_s < 1.5, (
        f"gave up after {busy.value.waited_s:.2f}s: the override no longer bounds the queue wait"
    )
