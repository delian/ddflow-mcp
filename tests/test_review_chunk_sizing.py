"""Chunk sizing: a reviewer's chunk is derived from its output cap and shrinks on a truncation.

Bug Bd8c4e1d8d6 -> DX-review-chunk-sizing: reviewers truncate at the reasoning cap on chunks
far under the ``max_chunk_chars`` quality bound. The chunk a reviewer is sent is the SMALLER
of its quality bound and its OUTPUT cap -- a reasoning model spends ``max_tokens`` thinking
before it emits a verdict at roughly one token per input character, so a chunk near the bound
can exhaust the cap mid-thought and come back TRUNCATED, spending a round. The sizing derives
the budget from the cap and shrinks (halves) a chunk that still truncates. The machine code
landed with fix-B9a2279db22; this file is the named guard that holds both behaviours.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ddflow.services import review as R
from ddflow.services.review import REVIEWED, Reviewer, review


def _files(*lines: str) -> str:
    return "".join(
        f"diff --git a/f{n}.py b/f{n}.py\n--- a/f{n}.py\n+++ b/f{n}.py\n@@ -0,0 +1 @@\n# {s}\n"
        for n, s in enumerate(lines)
    )


def _sent_diff(user: str) -> str:
    """The raw diff a rendered request carries, between the review_user fences."""
    _, _, tail = user.partition("```diff\n")
    if not tail:
        return user
    return tail if (idx := tail.rfind("\n```")) < 0 else tail[:idx]


def test_a_chunk_budget_derives_from_the_output_cap():
    """The budget is the smaller of the quality bound and the OUTPUT cap -- a cap under the
    bound wins, a bound inside the cap is never grown up to the cap."""
    assert R._chunk_budget(Reviewer(name="r", max_tokens=8000, max_chunk_chars=30000)) == 8000
    assert R._chunk_budget(Reviewer(name="r", max_tokens=32000, max_chunk_chars=30000)) == 30000
    assert R._chunk_budget(Reviewer(name="r", max_tokens=100000, max_chunk_chars=120)) == 120


def test_a_diff_is_split_to_fit_the_output_cap_before_it_is_sent(monkeypatch):
    """A diff larger than the output cap is split into chunks each within that cap, never one
    chunk that outgrows the budget and truncates mid-thought."""
    rev = Reviewer(name="r", base_url="http://x", model="m", max_tokens=5000, hedge=1)
    diff = _files(*[f"line {n:03d}" for n in range(100)])
    sent: list[str] = []

    def race(reviewer, system, users, started, **kw):
        sent.extend(users)
        return [("STATUS: NO FINDINGS", "")] * len(users)

    monkeypatch.setattr(R, "_race", race)
    res = review(rev, diff, "i")
    assert res.max_chunk_chars == rev.max_tokens == 5000, res.max_chunk_chars
    assert len(sent) == res.chunks_total > 1, "the over-cap diff was split"
    assert max(len(_sent_diff(u)) for u in sent) <= res.max_chunk_chars
    assert res.status == REVIEWED, res.reason


def test_a_chunk_that_truncates_is_retried_shrunk_into_halves(monkeypatch):
    """shrink on a truncation, through the review path: a chunk that ran out of output budget
    is retried as TWO SMALLER single-file pieces, never re-sent at the size that truncated."""
    # One two-file chunk that CAN halve: the two files fit under one budget, and a chunk lost
    # to its size splits into one piece per file.
    rev = Reviewer(name="r", base_url="http://x", model="m", max_chunk_chars=500, hedge=1)
    diff = _files("a" * 80, "b" * 80)
    first: list[str] = []
    retried: list[str] = []

    def race(reviewer, system, users, started, **kw):
        if not first:
            first.extend(_sent_diff(u) for u in users)
            return [("", R.TRUNCATED)] * len(users)
        retried.extend(_sent_diff(u) for u in users)
        return [("STATUS: NO FINDINGS", "")] * len(users)

    monkeypatch.setattr(R, "_race", race)
    res = review(rev, diff, "i")
    assert len(first) == 1, "the two files fit in one chunk"
    assert len(retried) == 2, "the truncated chunk halves into two single-file pieces"
    assert max(len(d) for d in retried) < len(first[0]), "each half is smaller than the whole"
    got = {ln.split(" ")[2] for d in retried for ln in d.splitlines() if ln.startswith("diff --git")}
    assert got == {"a/f0.py", "a/f1.py"}, "both files survive the shrink; none re-sent whole"
    assert res.status == REVIEWED, res.reason
