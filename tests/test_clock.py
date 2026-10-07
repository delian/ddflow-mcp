"""B-uni-clock: one time module (`ddflow.core.clock`).

PINNED FIRST (D-unify 4): the table below is what every timestamp helper returned
BEFORE the parsers moved into `core.clock`, captured on the old code over every shape
the log and the exports meet -- with and without microseconds, `Z`, `+00:00`, another
offset, `+0000`, zone-less, date only, padded, garbage, empty. The helpers keep their
names (each now a thin caller of `clock`) and must return exactly this. The table runs
under a non-UTC zone, so "zone-less means UTC" and "zone-less means host-local" are
visibly different answers, and each caller keeps its own.
"""

from __future__ import annotations

import time
from datetime import UTC, date, datetime

import pytest

from ddflow.api.reporting import records
from ddflow.core import clock, progress
from ddflow.services import quota, sessions, triggers
from ddflow.services.export import kind_bugs, query

INF = float("inf")
NOW = 1791334923.0  # 2026-10-07T01:02:03Z

#: shape -> (progress.epoch, records._epoch, sessions._age_s(NOW), triggers.ts,
#: query._parse_ts, quota._parse_time, kind_bugs._age_days("2026-10-01...", shape)).
#: A string starting with "!" is the exception the helper raised.
PINNED = [
    (
        "2026-10-07T01:02:03.456789Z",
        1791334923.456789,
        1791334923.456789,
        -0.4567890167236328,
        "2026-10-07T01:02:03.456789+00:00",
        "2026-10-07T01:02:03.456789+00:00",
        "2026-10-07T01:02:03.456789+00:00",
        6,
    ),
    (
        "2026-10-07T01:02:03Z",
        1791334923.0,
        1791334923.0,
        0.0,
        "2026-10-07T01:02:03+00:00",
        "2026-10-07T01:02:03+00:00",
        "2026-10-07T01:02:03+00:00",
        6,
    ),
    (
        "2026-10-07T01:02:03.456789+00:00",
        1791334923.456789,
        1791334923.456789,
        -0.4567890167236328,
        "2026-10-07T01:02:03.456789+00:00",
        "2026-10-07T01:02:03.456789+00:00",
        "2026-10-07T01:02:03.456789+00:00",
        6,
    ),
    (
        "2026-10-07T01:02:03+00:00",
        1791334923.0,
        1791334923.0,
        0.0,
        "2026-10-07T01:02:03+00:00",
        "2026-10-07T01:02:03+00:00",
        "2026-10-07T01:02:03+00:00",
        6,
    ),
    (
        "2026-10-07T01:02:03+02:00",
        1791327723.0,
        1791327723.0,
        7200.0,
        "2026-10-07T01:02:03+02:00",
        "2026-10-07T01:02:03+02:00",
        "2026-10-07T01:02:03+02:00",
        6,
    ),
    (
        "2026-10-07T01:02:03.456789",
        1791315123.456789,
        1791315123.456789,
        -0.4567890167236328,
        "2026-10-07T01:02:03.456789+00:00",
        "2026-10-07T01:02:03.456789+00:00",
        "!QuotaError",
        6,
    ),
    (
        "2026-10-07T01:02:03",
        1791315123.0,
        1791315123.0,
        0.0,
        "2026-10-07T01:02:03+00:00",
        "2026-10-07T01:02:03+00:00",
        "!QuotaError",
        6,
    ),
    (
        "2026-10-07",
        1791311400.0,
        1791311400.0,
        3723.0,
        "2026-10-07T00:00:00+00:00",
        "2026-10-07T00:00:00+00:00",
        "!QuotaError",
        6,
    ),
    (
        "2026-10-07T01:02:03.5Z",
        1791334923.5,
        1791334923.5,
        -0.5,
        "2026-10-07T01:02:03.500000+00:00",
        "2026-10-07T01:02:03.500000+00:00",
        "2026-10-07T01:02:03.500000+00:00",
        6,
    ),
    (
        "2026-10-07T01:02:03.456789+0000",
        1791334923.456789,
        1791334923.456789,
        -0.4567890167236328,
        "2026-10-07T01:02:03.456789+00:00",
        "2026-10-07T01:02:03.456789+00:00",
        "2026-10-07T01:02:03.456789+00:00",
        6,
    ),
    (
        " 2026-10-07T01:02:03Z ",
        0.0,
        0.0,
        INF,
        None,
        "2026-10-07T01:02:03+00:00",
        "!QuotaError",
        None,
    ),
    ("garbage", 0.0, 0.0, INF, None, "!ValueError", "!QuotaError", None),
    ("", 0.0, 0.0, INF, None, "!ValueError", "!QuotaError", None),
]


@pytest.fixture(autouse=True)
def _kolkata(monkeypatch):
    monkeypatch.setenv("TZ", "Asia/Kolkata")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def _iso(t):
    return t.isoformat() if t else None


def _call(fn):
    try:
        return fn()
    except Exception as exc:  # the pinned value of a raising helper is its exception
        return "!" + type(exc).__name__


@pytest.mark.parametrize(("shape", *"abcdefg"), PINNED, ids=[repr(r[0]) for r in PINNED])
def test_every_helper_returns_what_it_returned_before_the_move(shape, a, b, c, d, e, f, g):
    got = (
        _call(lambda: progress.epoch(shape)),
        _call(lambda: records._epoch(shape)),
        _call(lambda: sessions._age_s(shape, NOW)),
        _call(lambda: _iso(triggers.ts(shape))),
        _call(lambda: query._parse_ts(shape).isoformat()),
        _call(lambda: quota._parse_time(shape)),
        _call(lambda: kind_bugs._age_days("2026-10-01T00:00:00Z", shape)),
    )
    assert got == (a, b, c, d, e, f, g)


# -- the module itself --------------------------------------------------------------


def test_now_iso_writes_the_logs_shape_at_either_precision():
    us, s = clock.now_iso(), clock.now_iso(timespec="seconds")
    assert len(us) == len("2026-10-07T01:02:03.456789Z") and us.endswith("Z")
    assert len(s) == len("2026-10-07T01:02:03Z") and s.endswith("Z")
    assert abs(clock.epoch(us) - time.time()) < 60


def test_a_naive_time_means_what_the_caller_says():
    naive = "2026-10-07T01:02:03"
    assert clock.parse_ts(naive).tzinfo is UTC
    assert clock.parse_ts(naive, naive="local").tzinfo is None
    with pytest.raises(clock.NoTimezone, match="no timezone"):
        clock.parse_ts(naive, naive="refuse")
    with pytest.raises(quota.QuotaError, match="has no timezone: add Z or an offset"):
        quota._parse_time(naive)
    assert clock.epoch(naive, naive="refuse", default=-1.0) == -1.0
    assert clock.age_s(naive, NOW, naive="refuse") == INF
    assert clock.epoch(naive, naive="local") - clock.epoch(naive) == -5.5 * 3600


def test_unreadable_input_takes_the_callers_default():
    for bad in ("garbage", "", " 2026-10-07T01:02:03Z ", None, 12):
        assert clock.epoch(bad, default=-1.0) == -1.0
        assert clock.age_s(bad, NOW) == INF
    assert clock.parse_ts(" 2026-10-07T01:02:03Z ", strip=True) == datetime(
        2026, 10, 7, 1, 2, 3, tzinfo=UTC
    )
    with pytest.raises(TypeError):
        clock.parse_ts(12)


def test_parse_date_is_a_calendar_day_only():
    assert clock.parse_date("2026-10-07") == date(2026, 10, 7)
    for bad in ("2026-13-01", "2026-10-07T01:02:03Z", ""):
        with pytest.raises(ValueError):
            clock.parse_date(bad)
