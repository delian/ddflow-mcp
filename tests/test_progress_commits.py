"""Regression: one landing is one commit in `ddflow progress`.

`merge` records the merge commit in worktree.merged, and `complete` without --sha
records the same commit in item.completed. Counting both made every completed task
report two commits.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_progress_loops import ev

from ddflow.core import progress as PR
from ddflow.core.model import fold


def _work(*tail):
    events = [
        ev(1, "task.added", "T1", {"title": "t"}),
        ev(2, "lease.acquired", "T1", {"holder": "a", "at": 1.0}),
        *tail,
    ]
    return PR.work(events, fold(events, strict=False))["T1"]


def test_same_sha_on_merge_and_complete_is_one_commit():
    w = _work(
        ev(3, "worktree.merged", "T1", {"sha": "aaa111"}),
        ev(4, "item.completed", "T1", {"sha": "aaa111"}),
    )
    assert w.commits == ["aaa111"]
    assert w.summary()["commits"] == 1


def test_different_complete_sha_is_a_second_commit():
    w = _work(
        ev(3, "worktree.merged", "T1", {"sha": "aaa111"}),
        ev(4, "item.completed", "T1", {"sha": "bbb222"}),
    )
    assert w.commits == ["aaa111", "bbb222"]
    assert w.summary()["commits"] == 2
