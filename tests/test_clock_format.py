"""Display formatting and the 'now' writers live in core.clock (B-uni-clock.2-format).

Each helper replaces an expression that was repeated at its call sites; the tables pin the
helper to that expression over every shape a stored timestamp takes, so the move changed
no output. A guard keeps the expressions from growing back elsewhere.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ddflow.core import clock

ROOT = Path(__file__).resolve().parents[1] / "ddflow"

#: Every shape a stored timestamp takes: microseconds, seconds, Z, an offset, naive, a
#: bare date, empty, and text that is not a time at all.
SHAPES = [
    "2026-10-07T01:02:03.456789Z",
    "2026-10-07T01:02:03Z",
    "2026-10-07T01:02:03+00:00",
    "2026-10-07T01:02:03+02:00",
    "2026-10-07T01:02:03",
    "2026-10-07",
    "",
    "garbage",
]


@pytest.mark.parametrize("ts", SHAPES)
def test_fmt_minute_is_the_old_slice(ts):
    assert clock.fmt_minute(ts) == ts[:16].replace("T", " ")


@pytest.mark.parametrize("ts", SHAPES)
def test_fmt_date_is_the_old_slice(ts):
    assert clock.fmt_date(ts) == ts[:10]


def test_fmt_minute_and_date_read_as_a_person_expects():
    assert clock.fmt_minute("2026-10-07T01:02:03.456789Z") == "2026-10-07 01:02"
    assert clock.fmt_date("2026-10-07T01:02:03Z") == "2026-10-07"


SECONDS = [0, 1, 59, 60, 61, 119.9, 3599, 3600, 86399, 86400, 129600, 1800.0]


@pytest.mark.parametrize("s", SECONDS)
def test_fmt_age_matches_every_old_rendering(s):
    # brief "waited 12m", heartbeat "12m so far": floor minutes.
    assert clock.fmt_age(s) == f"{int(s) // 60}m"
    # enforce's lapsed-lease note: "LAPSED 3 min ago", "for 5 min", "30 min TTL".
    assert clock.fmt_age(s, "min") == f"{int(s // 60)} min"
    # progress: "12.3m" held.
    assert clock.fmt_age(s, places=1) == f"{s / 60:.1f}m"
    # cadence: "1.5 days" since the last run.
    assert clock.fmt_age(s, "days", places=1) == f"{s / 86400:.1f} days"


def test_fmt_age_floors_whole_units():
    assert clock.fmt_age(779) == "12m"  # 12 min 59 s is never shown as 13
    assert clock.fmt_age(7199, "h") == "1h"


def test_fmt_age_refuses_an_unknown_unit():
    with pytest.raises(KeyError):
        clock.fmt_age(60, "fortnights")


T = 1791334923.456789  # 2026-10-07T01:02:03.456789Z


def test_iso_at_writes_the_log_form():
    want = datetime.fromtimestamp(T, tz=UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    assert clock.iso_at(T) == want
    assert clock.iso_at(T, timespec="seconds") == want[:19] + "Z"


def test_compact_at_is_the_gate_log_stamp():
    assert clock.compact_at(T) == datetime.fromtimestamp(T, UTC).strftime("%Y%m%dT%H%M%S%fZ")
    # The gate runner matches its own log names with this pattern.
    assert re.fullmatch(r"\d{8}T\d{6}(\d{6})?Z", clock.compact_at())


def test_fmt_time_is_the_utc_time_of_day():
    assert clock.fmt_time(T) == datetime.fromtimestamp(T, tz=UTC).strftime("%H:%M:%SZ")


def test_now_utc_is_aware_utc():
    now = clock.now_utc()
    assert now.tzinfo is UTC
    assert abs(now.timestamp() - datetime.now(UTC).timestamp()) < 5


#: The expressions core.clock replaced, which must not grow back anywhere else.
_BANNED = {
    "minute slice": re.compile(r"\[:16\]\.replace\(\s*['\"]T['\"]"),
    "datetime.now": re.compile(r"\bdatetime\.(now|utcnow)\("),
}


def test_the_replaced_expressions_live_only_in_core_clock():
    files = sorted(ROOT.rglob("*.py"))
    # A scan of nothing would pass: prove the checkout's package was the one scanned.
    assert (ROOT / "core" / "clock.py") in files and len(files) > 100, ROOT
    found = [
        f"{p.relative_to(ROOT.parent)}:{n}: {name}"
        for p in files
        if p != ROOT / "core" / "clock.py"
        for n, line in enumerate(p.read_text("utf-8").splitlines(), 1)
        for name, rx in _BANNED.items()
        if rx.search(line)
    ]
    assert not found, "use core.clock (fmt_minute / now_utc / now_iso):\n" + "\n".join(found)
