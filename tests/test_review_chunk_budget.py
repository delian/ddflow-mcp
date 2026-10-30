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
    """The diff must be split into chunks each within the reviewer's (verdict-reserved)
    output cap, not one chunk too large that truncates and wastes a round."""
    rev = Reviewer(name="r", base_url="http://x", model="m", max_tokens=8000, hedge=1)
    diff = _files(*[f"line {n:03d}" for n in range(100)])
    sent_sizes: list[int] = []
    real_split = R.split_diff

    def recording_split(piece, max_chars):
        parts = real_split(piece, max_chars)
        sent_sizes.extend(len(p) for p in parts)
        return parts

    def race(reviewer, system, users, started, **kw):
        return [("STATUS: NO FINDINGS", "")] * len(users)

    monkeypatch.setattr(R, "split_diff", recording_split)
    monkeypatch.setattr(R, "_race", race)

    res = review(rev, diff, "i")

    budget = R._chunk_budget(rev)
    assert budget <= rev.max_tokens - R.VERDICT_RESERVE_TOKENS, (
        "the chunk budget must leave room for the verdict"
    )
    assert len(diff) > budget, "the fixture diff must outgrow the acceptable budget"
    assert res.max_chunk_chars == budget
    assert res.chunks_total > 1, "an over-budget diff must be split before it is sent"
    assert max(sent_sizes) <= budget, "no chunk may be sent larger than the accepted budget"
    assert res.status == REVIEWED, res.reason


def test_the_quality_bound_wins_when_it_is_tighter():
    """``max_chunk_chars`` below the (verdict-reserved) output cap is kept; only a chunk that
    would outgrow the output cap is shrunk. The lower bound never grows a chunk up to
    ``max_tokens``."""
    assert R._chunk_budget(Reviewer(name="r", max_chunk_chars=120)) == 120
    assert R._chunk_budget(Reviewer(name="r", max_tokens=100000, max_chunk_chars=120)) == 120
