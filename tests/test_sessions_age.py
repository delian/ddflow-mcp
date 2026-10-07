"""A session timestamp's age, whatever precision it was written with (Bbf85f6576f).

`sessions._age_s` parsed only `%Y-%m-%dT%H:%M:%S.%fZ` and called anything else infinitely
old, so a valid ISO timestamp without microseconds (written by `quota._now`, an import or
by hand) aged a fresh session out at once.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ddflow.services import sessions as S

THEN = 1_790_000_000.0  # 2026-09-21T14:13:20Z


@pytest.mark.parametrize(
    "ts",
    ["2026-09-21T14:13:20.000000Z", "2026-09-21T14:13:20Z", "2026-09-21T14:13:20+00:00"],
)
def test_the_age_is_the_same_for_every_precision(ts):
    assert S._age_s(ts, now=THEN + 90) == pytest.approx(90)


def test_an_unparseable_timestamp_is_still_infinitely_old():
    assert S._age_s("yesterday", now=THEN) == float("inf")
    assert S._age_s("", now=THEN) == float("inf")
