"""`--force` is past the round budget, not a full round (bug Ba93aec8448): an extra round the
operator allowed can be a delta, re-sending only what changed instead of the whole diff."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_review_delta import _ev, _fix, _review, _setup


def test_a_forced_extra_round_may_be_a_delta(repo, tmp_path):
    _setup(repo, tmp_path, "[review]\nmax_rounds = 1\n")
    assert _review(repo).data["outcome"] == "failed"  # round 1: the budget is spent
    _fix(repo)
    out = _review(repo, delta=True, force=True, reason="one more, only what changed")
    assert out.exit == 0 and "contradict" not in out.reason, out.reason
    assert _ev(repo)["review_kind"] == "delta", _ev(repo)


def test_full_and_delta_still_contradict(repo, tmp_path):
    _setup(repo, tmp_path)
    _review(repo)
    out = _review(repo, delta=True, full=True)
    assert out.exit != 0 and "--full and --delta contradict" in out.reason, out.reason


def test_a_delta_force_review_says_delta_not_the_whole_diff(repo, tmp_path):
    """Ba32fcedea6: a --delta re-review printed "re-review: the whole diff" while sending
    only the delta, so a reader believed the whole diff was reviewed."""
    _setup(repo, tmp_path, "[review]\nmax_rounds = 1\n")
    assert _review(repo).data["outcome"] == "failed"  # round 1 spends the budget (FINDME)
    _fix(repo)
    lines: list[str] = []
    out = _review(
        repo,
        delta=True,
        force=True,
        reason="one more, only what changed",
        on_progress=lines.append,
    )
    assert out.exit == 0, out.reason
    joined = "\n".join(lines)
    assert "re-review:" in joined, joined
    assert "the whole diff" not in joined, joined
    assert "delta" in joined, joined
