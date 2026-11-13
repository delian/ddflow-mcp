"""The kill-unwind switch: what a controller decides for a LIVE lane it is about to kill.

The active counterpart to `recover_auto`: stop the subagent, release the lease, and in
one switch either preserve the worktree (soft unwind) or discard it (hard kill). A hard
kill never discards an adopted harness tree or a tree it could not measure -- it falls
back to a soft unwind -- so no branch throws work away that ddflow had no right or no
answer to throw away.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ddflow.services.lanes.unwind import (
    HARD,
    SOFT,
    Kill,
    decide,
    decisions,
)


def _row(item, lane, hard=False, **kw):
    return {"item": item, "lane": lane, "hard": hard, **kw}


def test_soft_unwind_stops_and_releases_but_preserves_the_worktree():
    d = decide("T1", "lane-a", hard=False)
    assert d.effect == SOFT and d.hard is False and d.preserve is True
    assert "stop the subagent" in d.rationale and "release the lease" in d.rationale
    assert "preserve the worktree" in d.rationale


def test_hard_kill_discards_the_worktree_on_purpose():
    d = decide("T1", "lane-a", hard=True, salvageable=False)
    assert d.effect == HARD and d.hard is True and d.preserve is False
    assert "discard the worktree" in d.rationale and "on purpose" in d.rationale


def test_a_hard_kill_never_discards_an_adopted_harness_tree():
    d = decide("T1", "lane-a", hard=True, salvageable=False, adopted=True)
    assert d.effect == SOFT and d.preserve is True
    assert "adopted" in d.rationale and "never discard" in d.rationale


def test_a_hard_kill_never_discards_an_unmeasurable_tree():
    d = decide("T1", "lane-a", hard=True, salvageable=None)
    assert d.effect == SOFT and d.preserve is True
    assert "could not be measured" in d.rationale


def test_the_switch_reads_one_decision_for_every_kill():
    rows = [
        _row("T1", "lane-soft"),  # soft: preserved
        _row("T2", "lane-hard", hard=True, salvageable=False),  # hard over discardable
        _row("T3", "lane-adopted", hard=True, salvageable=False, adopted=True),  # fall back
        _row("T4", "lane-unknown", hard=True, salvageable=None),  # fall back
    ]
    got = decisions(rows)
    assert [d.item for d in got] == [r["item"] for r in rows]
    assert [d.effect for d in got] == [SOFT, HARD, SOFT, SOFT]
    assert [d.preserve for d in got] == [True, False, True, True]
    # Every kill stops the subagent and releases the lease whatever the switch.
    for d in got:
        assert "stop the subagent" in d.rationale
        assert "release the lease" in d.rationale


def test_a_present_false_adopted_beats_the_adopted_items_fallback():
    """A row's own ``adopted`` wins where both exist, even when it is False: a lane
    measured as not-adopted is hard-killed rather than silently preserved."""
    rows = [
        _row("T1", "lane-own-false", hard=True, salvageable=False, adopted=False),
        _row("T2", "lane-borrowed", hard=True, salvageable=False),
    ]
    got = decisions(rows, adopted_items={"T1": True, "T2": True})
    assert got[0].effect == HARD  # own False won: discardable and not adopted
    assert got[1].effect == SOFT  # fallback adopted: preserved


def test_kill_decisions_serialise_every_position():
    d = decide("T1", "lane-a", hard=False)
    assert set(d.as_dict()) == {"item", "lane", "effect", "rationale", "hard", "preserve"}
    assert isinstance(Kill, type)
