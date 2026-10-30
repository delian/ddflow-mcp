"""The chunk a reviewer is sent is sized from its declared output cap (B9a2279db22).

A reviewer declares ``max_tokens`` -- its OUTPUT cap -- and a reasoning model spends that
budget thinking before it emits a verdict. ``max_chunk_chars`` is a bound chosen for review
QUALITY, not a promise the model can answer a chunk that size: measured on
Qwen3.8-Flash-Next, a 30 KB chunk needed ~28k REASONING tokens (~1 per input character), so
a chunk sized near ``max_chunk_chars`` while ``max_chunk_chars`` exceeds ``max_tokens`` can
exhaust the output cap mid-thought and come back TRUNCATED -- which ddflow records
UNAVAILABLE and one review round is spent. The dispatch sizes the chunk from the output cap,
so a reviewer whose ``max_tokens`` is below ``max_chunk_chars`` gets its chunk split to fit
it BEFORE it is sent.
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


def test_a_chunk_is_split_to_fit_the_reviewers_output_cap(monkeypatch):
    """A diff larger than the reviewer's output cap is SPLIT into chunks each within that
    cap -- not one chunk too large that truncates mid-thought and wastes a round."""
    rev = Reviewer(name="r", base_url="http://x", model="m", max_tokens=5000, hedge=1)
    diff = _files(*[f"line {n:03d}" for n in range(100)])
    sent: list[str] = []  # the rendered requests actually handed to the reviewer
    split_with: list[tuple[int, list[int]]] = []
    real_split = R.split_diff

    def recording_split(piece, max_chars):
        parts = real_split(piece, max_chars)
        split_with.append((max_chars, [len(p) for p in parts]))
        return parts

    def race(reviewer, system, users, started, **kw):
        sent.extend(users)
        return [("STATUS: NO FINDINGS", "")] * len(users)

    monkeypatch.setattr(R, "split_diff", recording_split)
    monkeypatch.setattr(R, "_race", race)

    res = review(rev, diff, "i")

    budget = R._chunk_budget(rev)
    assert budget == rev.max_tokens, "the chunk budget is the output cap, not max_chunk_chars"
    assert res.max_chunk_chars == budget
    (used, sizes) = split_with[0]
    assert used == budget, "the dispatch split with the output-cap budget, not max_chunk_chars"
    assert len(sizes) == res.chunks_total > 1, "the over-budget diff was split, not sent whole"
    assert max(sizes) <= budget, "no chunk is cut larger than the accepted budget"
    sent_diffs = [_sent_diff(u) for u in sent]
    assert len(sent) == res.chunks_total > 1 and max(len(d) for d in sent_diffs) <= budget
    assert res.status == REVIEWED, res.reason


def test_a_chunk_is_never_bigger_than_the_output_cap():
    """Only a chunk that would outgrow the output cap is shrunk; the lower bound never grows
    a chunk up to ``max_tokens``."""
    # a small output cap wins over a larger quality bound
    assert R._chunk_budget(Reviewer(name="r", max_tokens=8000, max_chunk_chars=30000)) == 8000
    # the quality bound already inside the output cap is kept
    assert R._chunk_budget(Reviewer(name="r", max_tokens=32000, max_chunk_chars=30000)) == 30000
    assert R._chunk_budget(Reviewer(name="r", max_tokens=100000, max_chunk_chars=120)) == 120
