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
        CI,
        "_execute",
        lambda cfg=None, tree=None, cmd=None, scope=None, budget=None, tick=None, holding_suite=False, used=None: (
            NS(kind="", ran=True),
            "",
        ),
    )
    CI._run_locked(Config(), None, "HEAD", None, "", None, 7200, None, {}, True, 7200.0)
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
