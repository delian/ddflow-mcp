"""DX-dispatch-plan: the pure dispatcher plan -- which items to start now, in what order.

The planner under test is `ddflow.services.dispatch.plan` (pure): advise only, so every test
arrives with synthetic items and lanes and reads the plan back. Regression on the item's own
spec: three free slots hand out the right three of five ready items in order; two items whose
globs overlap are never offered together; a bug fix outranks a feature; a stalled lane is a
RESUME.
"""

from __future__ import annotations

from ddflow.services.dispatch.plan import RESUME, fill, plan, sort_ready, steer
from ddflow.services.lanes.advise import Move as AdviseMove
from ddflow.services.swarm import score
from ddflow.services.swarm.score_config import Weights


def _lane(name: str, cap: int = 1, family: str = "family") -> score.Lane:
    return score.Lane(name=name, family=family, model=f"model-{name}", cap=cap)


def _lanes(n: int, cap: int = 1) -> list[score.Lane]:
    return [_lane(f"lane{i}", cap=cap) for i in range(n)]


def test_sorted_puts_bug_fixes_first_then_priority_then_id():
    items = [score.Item(id="feat-low"), score.Item(id="bug-b"), score.Item(id="feat-high")]
    order = sort_ready(
        items, bugs=("bug-b",), priorities={"feat-low": 1, "bug-b": 2, "feat-high": 9}
    )
    assert [i.id for i in order] == ["bug-b", "feat-high", "feat-low"]


def test_three_free_slots_return_the_right_three_in_order():
    # Five ready items, three free slots, three lanes each at cap 1: the first three items
    # in priority order start, one per lane.
    items = [
        score.Item(id="a", globs=("pkg/a.py",)),
        score.Item(id="b", globs=("pkg/b.py",)),
        score.Item(id="c", globs=("pkg/c.py",)),
        score.Item(id="d", globs=("pkg/d.py",)),
        score.Item(id="e", globs=("pkg/e.py",)),
    ]
    result = plan(items, _lanes(3), score.World(), 3, Weights(), priorities=dict.fromkeys("abcde", 9))
    assert [s.item for s in result.starts] == ["a", "b", "c"]
    assert result.free == 3


def test_overlapping_items_are_never_returned_together():
    items = [
        score.Item(id="a", globs=("pkg/x.py",)),
        score.Item(id="b", globs=("pkg/x.py",)),  # overlaps a
        score.Item(id="c", globs=("pkg/c.py",)),
    ]
    result = plan(items, _lanes(2), score.World(), 2, Weights())
    started = {s.item for s in result.starts}
    assert "a" in started and "b" not in started
    assert "c" in started
    assert not ({"a", "b"} <= started)  # never both of the overlapping pair


def test_a_bug_fix_outranks_a_feature():
    bug = score.Item(id="fix-bug", globs=("pkg/bug.py",))
    feat = score.Item(id="build-feature", globs=("pkg/feat.py",))
    result = plan([feat, bug], _lanes(1), score.World(), 1, Weights(), bugs=("fix-bug",))
    # only one slot: the bug must take it, not the feature
    assert [s.item for s in result.starts] == ["fix-bug"]


def test_fill_is_bounded_by_free_slots():
    items = [score.Item(id=i, globs=(f"pkg/{i}.py",)) for i in "abcd"]
    starts, _world = fill(items, _lanes(4), score.World(), 2, Weights())
    assert [s.item for s in starts] == ["a", "b"]


def test_a_stalled_lane_is_a_resume_move():
    stalled = AdviseMove("resume", "lane x stopped with it unfinished", ("x",), "ddflow lanes brief x")
    moves = steer([stalled, AdviseMove("idle", "spare capacity", ("y",), "ddflow next")])
    verbs = [(m.verb, m.lane) for m in moves]
    assert (RESUME, "x") in verbs
    assert not any(v[0] == "idle" for v in verbs)  # idle is spare capacity, not a steer
