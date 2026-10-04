"""B-critic-concurrent-chunks: the chunks of one review run concurrently.

The shipped mechanism is `[[reviewer]].max_concurrency` (0 = one wave, N = cap, 1 = the old
one-chunk-at-a-time behaviour). These tests pin the three properties the item asked for:
results in chunk order, `total_budget_s` as WALL CLOCK rather than a sum, and per-chunk
partial accounting unchanged. They reuse the fake server of test_review_parallel.
"""

from __future__ import annotations

import time

from tests.test_review_parallel import _diff, _Fake, _rev  # noqa: F401
from tests.test_review_parallel import fake  # noqa: F401  (pytest fixture)

from ddflow.services.review import PARTIAL, REVIEWED, review


def _timed(rev, diff):
    t = time.time()
    res = review(rev, diff, "i")
    return res, time.time() - t


def test_max_concurrency_one_is_sequential_and_larger_is_faster(fake):
    diff = _diff(*["DELAY=0.5"] * 4)
    seq, t_seq = _timed(_rev(fake.url, hedge=1, max_concurrency=1), diff)
    assert seq.status == REVIEWED and fake.peak == 1
    fake.peak = 0
    par, t_par = _timed(_rev(fake.url, hedge=1, max_concurrency=4), diff)
    assert par.status == REVIEWED and fake.peak == 4
    assert t_seq >= 2.0 and t_par < t_seq / 2  # sum of chunks vs the slowest chunk


def test_total_budget_is_wall_clock_not_the_sum(fake):
    diff = _diff(*["DELAY=1"] * 4)  # 4 s of work in all
    par, _ = _timed(_rev(fake.url, hedge=1, max_concurrency=4, total_budget_s=3), diff)
    assert par.status == REVIEWED and par.chunks_reviewed == 4, par.reason
    seq, _ = _timed(_rev(fake.url, hedge=1, max_concurrency=1, total_budget_s=2), diff)
    assert seq.status == PARTIAL and seq.chunks_reviewed < 4  # the sum overruns the budget


def test_a_failed_chunk_is_partial_and_the_rest_keep_their_findings_in_order(fake):
    res = review(
        _rev(fake.url, hedge=1, max_concurrency=3),
        _diff("FINDING=a DELAY=0.4", "OFF_CONTRACT", "FINDING=c"),
        "i",
    )
    assert res.status == PARTIAL and res.chunks_reviewed == 2 and res.chunks_off_contract == 1
    assert [f.detail.strip() for f in res.findings] == ["a", "c"]
