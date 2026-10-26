"""Ba5d5d63f12: `gate run ... ci --timeout N` replaced the ci command's budget but not the
host suite-lock wait (`[ci].suite_wait_s`, 1800 s), so a queued run still gave up UNAVAILABLE
at the knob while the per-run override had authorised a much longer run. The override now
raises the lock-wait floor to the longer of the two, and a give-up at that longer wait still
parks the ticket, so a retry keeps its queue place."""

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
    cfg = Config()
    cfg.ci.suite_wait_s = 1800
    assert CI._lock_wait(cfg, 7200) == 7200.0  # a longer override extends the knob
    assert CI._lock_wait(cfg, 100) == 100.0  # a shorter override bounds the queued phase too
    assert CI._lock_wait(cfg, 0) is None  # no override: the knob alone decides


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
