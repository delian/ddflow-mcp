"""Quota evaluation: per-window figures from profile and readings, and the pause decision."""

from __future__ import annotations

import pytest

from ddflow.core import clock
from ddflow.services import meters_base as B
from ddflow.services import quota
from ddflow.services import quota_eval as Q

NOW = clock.epoch("2026-11-12T12:00:00Z")
RESET = "2026-11-12T15:00:00+00:00"


def reading(window="5h", unit="percent", value=50.0, stale=False, resets=RESET, status=B.OK):
    kw = {"value": value} if status == B.OK else {"value": None, "reason": "no data"}
    return B.Reading(
        source="claude.cache",
        harness="claude",
        window=window,
        unit=unit,
        status=status,
        observed_at="2026-11-12T11:59:00+00:00",
        stale=stale,
        resets_at=resets,
        **kw,
    )


def profile(*windows, **kw):
    return quota.Profile("agent:claude/local", windows=tuple(windows), **kw)


W5H = quota.Window("5h", 100, "percent")


def test_a_window_reports_used_fraction_reset_and_source():
    (e,) = Q.evaluate(profile(W5H), [reading(value=40.0)], NOW)
    assert (e.used, e.fraction, e.resets_at, e.stale) == (40.0, 0.4, RESET, False)
    assert e.source == "claude.cache"


def test_the_limit_scales_the_fraction():
    w = quota.Window("day", 1000, "tokens")
    (e,) = Q.evaluate(profile(w), [reading("day", "tokens", 250.0)], NOW)
    assert e.fraction == 0.25


@pytest.mark.parametrize(
    "p", [quota.Profile("a", unlimited=True), quota.Profile("a", unknown=True)]
)
def test_unlimited_and_unknown_never_pause(p):
    assert Q.evaluate(p, [reading(value=99.0)], NOW) == ()
    assert Q.decide((), 0.9, NOW, 60).action == "run"


def test_an_unmeasured_window_has_no_fraction_and_does_not_pause():
    (e,) = Q.evaluate(profile(W5H), [], NOW)
    assert e.fraction is None and e.stale and e.reason
    assert Q.decide((e,), 0.5, NOW, 60).action == "run"


def test_an_unavailable_reading_says_why():
    (e,) = Q.evaluate(profile(W5H), [reading(status=B.UNAVAILABLE)], NOW)
    assert e.fraction is None and e.reason == "no data"


def test_over_the_line_pauses_until_the_reset():
    ev = Q.evaluate(profile(W5H), [reading(value=95.0)], NOW)
    d = Q.decide(ev, 0.9, NOW, 60)
    assert d.action == "pause" and d.windows == ("5h",)
    assert d.resume_at == clock.epoch(RESET) and "95%" in d.reason


def test_exactly_at_the_line_pauses_and_just_below_does_not():
    assert (
        Q.decide(Q.evaluate(profile(W5H), [reading(value=90.0)], NOW), 0.9, NOW, 60).action
        == "pause"
    )
    assert (
        Q.decide(Q.evaluate(profile(W5H), [reading(value=89.9)], NOW), 0.9, NOW, 60).action == "run"
    )


def test_no_known_reset_rechecks_after_the_interval():
    ev = Q.evaluate(profile(W5H), [reading(value=95.0, resets="")], NOW)
    assert Q.decide(ev, 0.9, NOW, 300).resume_at == NOW + 300


def test_a_reset_already_past_rechecks_after_the_interval_not_at_once():
    ev = Q.evaluate(profile(W5H), [reading(value=95.0, resets="2026-11-12T11:00:00+00:00")], NOW)
    assert Q.decide(ev, 0.9, NOW, 300).resume_at == NOW + 300


def test_a_window_over_the_line_with_no_reset_ahead_of_it_rechecks_too():
    w2 = quota.Window("week", 100, "percent")
    rs = [reading(value=95.0), reading("week", value=99.0, resets="")]
    d = Q.decide(Q.evaluate(profile(W5H, w2), rs, NOW), 0.9, NOW, 300)
    assert d.resume_at == NOW + 300


def test_a_zero_limit_is_refused_not_divided_by():
    with pytest.raises(ValueError):
        Q.evaluate(profile(quota.Window("day", 0, "tokens")), [reading("day", "tokens", 0.0)], NOW)


def test_the_latest_reset_among_the_windows_over_the_line_wins():
    w2 = quota.Window("week", 100, "percent")
    rs = [reading(value=95.0), reading("week", value=99.0, resets="2026-11-15T00:00:00+00:00")]
    d = Q.decide(Q.evaluate(profile(W5H, w2), rs, NOW), 0.9, NOW, 60)
    assert d.windows == ("5h", "week") and d.resume_at == clock.epoch("2026-11-15T00:00:00+00:00")


def test_a_stale_figure_over_the_line_still_pauses():
    ev = Q.evaluate(profile(W5H), [reading(value=95.0, stale=True)], NOW)
    assert Q.decide(ev, 0.9, NOW, 60).action == "pause"


def test_resume_needs_a_fresh_figure_below_the_line():
    fresh = Q.evaluate(profile(W5H), [reading(value=10.0)], NOW)
    stale = Q.evaluate(profile(W5H), [reading(value=10.0, stale=True)], NOW)
    missing = Q.evaluate(profile(W5H), [], NOW)
    over = Q.evaluate(profile(W5H), [reading(value=95.0)], NOW)
    assert [Q.resume(x, 0.9) for x in (fresh, stale, missing, over)] == [True, False, False, False]


def test_a_fixed_window_resets_at_its_next_period_boundary():
    w = quota.Window("day", 100, "percent", reset="fixed", anchor="2026-11-01T06:00:00+00:00")
    (e,) = Q.evaluate(profile(w), [reading("day", resets="")], NOW)
    assert clock.epoch(e.resets_at) == clock.epoch("2026-11-13T06:00:00+00:00")


@pytest.mark.parametrize("bad", [0, -0.1, 1.5])
def test_a_pause_line_outside_the_unit_interval_is_refused(bad):
    with pytest.raises(ValueError):
        Q.decide((), bad, NOW, 60)


def test_a_non_positive_recheck_is_refused():
    with pytest.raises(ValueError):
        Q.decide((), 0.9, NOW, 0)


def test_a_stale_reading_with_a_past_reset_gives_way_to_the_fixed_boundary():
    w = quota.Window("day", 100, "percent", reset="fixed", anchor="2026-11-01T06:00:00+00:00")
    (e,) = Q.evaluate(profile(w), [reading("day", resets="2026-11-12T06:00:00+00:00")], NOW)
    assert clock.epoch(e.resets_at) == clock.epoch("2026-11-13T06:00:00+00:00")


def test_a_nan_recheck_is_refused():
    with pytest.raises(ValueError):
        Q.decide((), 0.9, NOW, float("nan"))
