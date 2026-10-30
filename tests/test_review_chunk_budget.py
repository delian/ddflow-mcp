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


def test_a_chunk_is_split_to_fit_the_reviewers_output_cap(monkeypatch):
    """The over-budget diff must be SPLIT into chunks each within the reviewer's
    (verdict-reserved) output cap, and each split piece must actually be SENT -- not one
    chunk too large that truncates and wastes a round."""
    rev = Reviewer(name="r", base_url="http://x", model="m", max_tokens=8000, hedge=1)
    diff = _files(*[f"line {n:03d}" for n in range(100)])
    sent: list[str] = []  # the prompts actually handed to the reviewer
    sizes: list[int] = []  # the chunk sizes split_diff produced
    real_split = R.split_diff

    def recording_split(piece, max_chars):
        parts = real_split(piece, max_chars)
        sizes.extend(len(p) for p in parts)
        return parts

    def race(reviewer, system, users, started, **kw):
        sent.extend(users)
        return [("STATUS: NO FINDINGS", "")] * len(users)

    monkeypatch.setattr(R, "split_diff", recording_split)
    monkeypatch.setattr(R, "_race", race)

    res = review(rev, diff, "i")

    budget = R._chunk_budget(rev)
    assert budget == rev.max_tokens - R.VERDICT_RESERVE_TOKENS
    assert res.max_chunk_chars == budget
    assert max(sizes) <= budget, "no chunk may be cut larger than the accepted budget"
    assert len(sent) == res.chunks_total == len(sizes) > 1, (
        "the over-budget diff was split, and each piece was sent as its own review request"
    )
    assert res.status == REVIEWED, res.reason


def test_the_quality_bound_wins_when_it_is_tighter():
    """``max_chunk_chars`` below the (verdict-reserved) output cap is kept; only a chunk that
    would outgrow the output cap is shrunk. A tiny output cap that cannot hold the reserve
    keeps ``max_chunk_chars`` rather than degrading into one-character chunks."""
    # max_chunk_chars below the reserved output cap is kept
    assert R._chunk_budget(Reviewer(name="r", max_tokens=100000, max_chunk_chars=120)) == 120
    # a default reviewer keeps its quality bound when that bound already leaves a verdict
    assert R._chunk_budget(Reviewer(name="r", max_chunk_chars=30000)) == 30000
    # a cap too small to reserve a verdict is not shrunk to the 1-char floor
    assert R._chunk_budget(Reviewer(name="r", max_tokens=1500, max_chunk_chars=30000)) == 30000
