"""LC-reuse: the freed lane takes the next item, measured (merged per hour, idle time)."""

from __future__ import annotations

import pytest

from ddflow.core.clock import epoch
from ddflow.core.events import Event
from ddflow.services.lanes.ledger import Lane
from ddflow.services.lanes.reuse import idle_time_s, merged_per_hour, next_for, released
from ddflow.services.lanes.states import WORKING


def _lane(state: str, *, item: str = "", since: float = 0.0) -> Lane:
    return Lane(name="t1", item=item, state=state, since=since)


def _merged(ts: str) -> Event:
    return Event(kind="worktree.merged", subject="T1", ts=ts)


def test_merged_per_hour_scales_the_window() -> None:
    # one merged item over a one-hour trailing window ending at now -> 1 per hour
    ts = "2026-11-10T20:00:00Z"
    now = epoch(ts) + 600.0  # ten minutes later
    assert merged_per_hour([_merged(ts)], 3600.0, now) == pytest.approx(1.0)


def test_merged_per_hour_ignores_items_outside_the_window() -> None:
    old = "2026-11-10T18:00:00Z"
    new = "2026-11-10T20:00:00Z"
    now = epoch(new)
    # only the new event is inside the one-hour window (the old one is 2h back)
    assert merged_per_hour([_merged(old), _merged(new)], 3600.0, now) == pytest.approx(1.0)


def test_merged_per_hour_decays_when_the_lane_is_quiet() -> None:
    # the newest merge is four hours old: anchored to `now`, the rate decays to nothing
    old = "2026-11-10T20:00:00Z"
    now = epoch("2026-11-11T00:00:00Z")
    assert merged_per_hour([_merged(old)], 3600.0, now) == pytest.approx(0.0)


def test_a_zero_window_is_refused_not_divided_by() -> None:
    with pytest.raises(ValueError):
        merged_per_hour([], 0.0, now=0.0)


def test_idle_time_is_zero_while_working_and_positive_when_free() -> None:
    assert idle_time_s(_lane(WORKING, item="T1", since=0.0), 1000.0) == 0.0
    assert idle_time_s(_lane("idle", since=100.0), 1000.0) == 900.0


def test_a_released_lane_gets_a_next_move_and_does_not_while_working() -> None:
    assert next_for(_lane(WORKING, item="T1", since=100.0), 1000.0) == ""
    assert "ddflow next" in next_for(_lane("idle", since=100.0), 1000.0)


def test_released_names_only_free_lanes() -> None:
    assert released([_lane(WORKING, item="T1"), _lane("idle")]) == [_lane("idle")]
