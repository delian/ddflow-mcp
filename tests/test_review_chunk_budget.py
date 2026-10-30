"""The chunk a reviewer is sent is sized from its declared output cap (B9a2279db22).

A reviewer declares ``max_tokens`` -- its OUTPUT cap -- and a reasoning model spends that
budget thinking before it emits a verdict. ``max_chunk_chars`` is a bound chosen for review
QUALITY, not a promise the model can answer a chunk that size: measured on
Qwen3.8-Flash-Next, a 30 KB chunk needed ~28k REASONING tokens (~1 per input character), so
a chunk near ``max_chunk_chars`` can exhaust the output cap mid-thought and come back
TRUNCATED -- which ddflow records UNAVAILABLE and one review round is spent. The dispatch
sizes the chunk from the output cap (minus room for the verdict), so a reviewer whose
``max_tokens`` is below ``max_chunk_chars`` gets its chunk split to fit it BEFORE it is sent.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

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
    head, _, tail = user.partition("```diff\n")
    if not tail:
        return user
    body = tail if (idx := tail.rfind("\n```")) < 0 else tail[:idx]
    return body


def test_a_chunk_is_split_to_fit_the_reviewers_output_cap(monkeypatch):
    """A diff larger than the reviewer's (verdict-reserved) output cap is SPLIT into chunks
    each within that cap and each sent on its own request -- not one chunk too large that
    truncates mid-thought and wastes a round."""
    rev = Reviewer(name="r", base_url="http://x", model="m", max_tokens=8000, hedge=1)
    diff = _files(*[f"line {n:03d}" for n in range(100)])
    sent: list[str] = []  # the rendered requests actually handed to the reviewer
    real_split = R.split_diff

    def recording_split(piece, max_chars):
        return real_split(piece, max_chars)

    def race(reviewer, system, users, started, **kw):
        sent.extend(users)
        return [("STATUS: NO FINDINGS", "")] * len(users)

    monkeypatch.setattr(R, "split_diff", recording_split)
    monkeypatch.setattr(R, "_race", race)

    res = review(rev, diff, "i")

    budget = R._chunk_budget(rev)
    assert budget == rev.max_tokens - R.VERDICT_RESERVE_TOKENS, "the chunk budget uses the output cap"
    assert res.max_chunk_chars == budget
    sent_diffs = [_sent_diff(u) for u in sent]
    assert len(sent) == res.chunks_total > 1, "the over-budget diff was split, and every piece was sent"
    assert max(len(d) for d in sent_diffs) <= budget, "no chunk is sent larger than the accepted budget"
    assert res.status == REVIEWED, res.reason


def test_an_undersized_output_cap_keeps_the_quality_bound():
    """A quality bound that already leaves the verdict room is kept; only a cap that would
    outgrow the output budget is shrunk below ``max_tokens`` to reserve the verdict. An
    output cap too small to hold the reserve is left alone, not shrunk to one chunk per
    line."""
    # the default reviewer keeps its quality bound (the reserve is already inside it)
    assert R._chunk_budget(Reviewer(name="r", max_tokens=32000, max_chunk_chars=30000)) == 30000
    # a small output cap is shrunk below max_tokens to leave the verdict room
    assert R._chunk_budget(Reviewer(name="r", max_tokens=8000, max_chunk_chars=30000)) == 6000
    # the quality bound below the reserved output cap is kept
    assert R._chunk_budget(Reviewer(name="r", max_tokens=100000, max_chunk_chars=120)) == 120
    # a cap just above the reserve never shrinks below one verdict reserve (not one chunk per line)
    assert R._chunk_budget(Reviewer(name="r", max_tokens=2001, max_chunk_chars=100000)) == 2000
    # an output cap too small to hold the reserve is left to the truncation retry
    assert R._chunk_budget(Reviewer(name="r", max_tokens=1000, max_chunk_chars=30000)) == 30000
