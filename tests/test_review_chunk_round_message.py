"""B-review-chunk-overran-bound: the retry round's progress line read '2132s elapsed; one
request may take up to 1800s' -- the total since the review began beside a per-request
bound, so a retry looked like a request that outlived its bound. The line now also says how
long the CURRENT round has run, which restarts with the retry."""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ddflow.services.review import review
from tests.test_review_split_nonconverging import _reviewer, _two_files

#: the whole chunk never answers; a half takes a while, then answers
SLOW_THEN_HALVES = (
    'case "$in" in *FILEA*FILEB*) sleep 20;; esac\n'
    'case "$in" in *FILEA*) sleep 1;; esac\n'
    "echo 'STATUS: NO FINDINGS'"
)


def test_a_retry_tick_reports_the_time_in_the_retry_round_not_only_since_the_start(tmp_path):
    rev, _ = _reviewer(tmp_path, SLOW_THEN_HALVES, timeout_s=2)
    lines: list[str] = []
    review(rev, _two_files(), "i", split_rounds=1, on_progress=lines.append, tick_s=0.2)
    ticks = [x for x in lines if "waiting" in x and "on retry" in x]
    assert ticks, lines
    total, this_round = (
        int(n) for n in re.search(r"(\d+)s elapsed, (\d+)s in this round", ticks[0]).groups()
    )  # type: ignore[union-attr]
    assert total >= 2 and this_round < total, ticks[0]
