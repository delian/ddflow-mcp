"""The chunk a reviewer is sent is sized from its declared output cap (B9a2279db22).

A reviewer declares ``max_tokens`` -- its OUTPUT cap -- and a reasoning model spends that
budget thinking before it emits a verdict. ``max_chunk_chars`` is a bound chosen for review
QUALITY, not a promise the model can answer a chunk that size: measured on
Qwen3.8-Flash-Next, a 30 KB chunk needed ~28k REASONING tokens (~1 per input character), so
a chunk near ``max_chunk_chars`` can exhaust a SMALL ``max_tokens`` mid-thought and come back
TRUNCATED -- which ddflow records UNAVAILABLE and one review round is spent. The dispatch
sizes the chunk from the output cap, so a reviewer whose ``max_tokens`` is below
``max_chunk_chars`` gets its chunk split to fit it BEFORE it is sent.
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
    """A reviewer whose output cap (``max_tokens``) is below ``max_chunk_chars`` must have
    its diff split into chunks small enough to reason about, not one chunk too large that
    truncates and wastes a round."""
    rev = Reviewer(name="r", base_url="http://x", model="m", max_tokens=120, hedge=1)
    diff = _files(*[f"line {n:03d}" for n in range(12)])
    sent: list[str] = []

    def race(reviewer, system, users, started, **kw):
        sent.extend(users)
        return [("STATUS: NO FINDINGS", "")] * len(users)

    monkeypatch.setattr(R, "_race", race)
    res = review(rev, diff, "i")

    assert len(diff) > rev.max_tokens, "the fixture diff must start out too large to fit the cap"
    assert res.max_chunk_chars == rev.max_tokens, (
        "the recorded chunk size must be the output cap, not the quality bound"
    )
    assert res.chunks_total > 1, "a diff too large for the output cap must be split before it is sent"
    assert res.status == REVIEWED, res.reason


def test_the_quality_bound_wins_when_it_is_tighter():
    """``max_chunk_chars`` below the output cap is kept; only a chunk that would outgrow the
    output cap is shrunk. The lower bound NEVER grows a chunk up to ``max_tokens``."""
    rev = Reviewer(name="r", base_url="http://x", model="m", max_tokens=100000, max_chunk_chars=120)
    assert R._chunk_budget(rev) == 120
