"""`duplicate_work` must not flag items that `needs` already orders.

Bug Bd1c601896f: in run_nemo_run, KNOWNFAIL.1 and KNOWNFAIL.2 both declare
`tests/known_failures.args`, and KNOWNFAIL.2 needs KNOWNFAIL.1. Doctor warned that the
two "cannot run in parallel" and asked the operator to compare them -- but the dependency
already serialises them, so the warning asked for action on nothing. Same files is a
problem only for a pair the scheduler could hand out at once.

"Ordered" means what readiness means: a dependency binds through `inherited_deps` (an
item's own `needs` plus its ancestors'), and a dependency on a phase waits for every
task in it. A dependency that no longer waits -- done, removed, unknown -- orders nothing.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.config import Config
from ddflow.core import progress as PR
from ddflow.core.model import fold


def _add(log, iid: str, kind: str = "task", **data) -> None:
    log.append(f"{kind}.added", iid, {"title": iid, "kind": kind, **data})


def _dupes(log, cfg: Config | None = None) -> list[PR.LoopFinding]:
    evs = log.read_all()
    found = PR.detect(evs, fold(evs, strict=False), cfg or Config())
    return [f for f in found if f.kind == "duplicate_work"]


def test_a_direct_dependency_orders_the_pair(log):
    _add(log, "KNOWNFAIL.1", globs=["tests/known_failures.args"])
    _add(log, "KNOWNFAIL.2", globs=["tests/known_failures.args"], needs=["KNOWNFAIL.1"])
    assert _dupes(log) == []


def test_a_transitive_dependency_through_an_item_outside_the_group_orders_the_pair(log):
    _add(log, "A", globs=["x.py"])
    _add(log, "M", globs=["other.py"], needs=["A"])
    _add(log, "B", globs=["x.py"], needs=["M"])
    assert _dupes(log) == []


def test_a_phase_dependency_orders_the_tasks_on_both_sides(log):
    """B's phase R needs phase P, and P cannot finish while A is open."""
    _add(log, "P", kind="phase")
    _add(log, "R", kind="phase", needs=["P"])
    _add(log, "A", parent="P", globs=["x.py"])
    _add(log, "B", parent="R", globs=["x.py"])
    assert _dupes(log) == []


def test_an_unordered_pair_is_still_flagged(log):
    _add(log, "160.D.4", globs=["m.py", "t.py"])
    _add(log, "160.D.5", globs=["m.py", "t.py"])
    [f] = _dupes(log)
    assert f.count == 2 and "160.D.4, 160.D.5" in f.detail, f.detail


def test_three_where_two_are_ordered_and_one_is_not_is_still_flagged(log):
    _add(log, "A", globs=["x.py"])
    _add(log, "B", globs=["x.py"], needs=["A"])
    _add(log, "C", globs=["x.py"])
    [f] = _dupes(log)
    # C can run beside A and beside B, so every member is in some unordered pair.
    assert f.count == 3 and "A, B, C" in f.detail, f.detail


def test_a_mixed_group_names_only_the_members_that_are_unordered(log):
    _add(log, "A", globs=["x.py"])
    _add(log, "B", globs=["x.py"], needs=["A"])
    _add(log, "C", globs=["x.py"], needs=["A"])
    [f] = _dupes(log)
    assert f.item == "B" and f.count == 2, f
    assert "B, C" in f.detail and "A, B" not in f.detail, f.detail


def test_the_threshold_counts_the_group_not_just_its_unordered_members(log):
    """Critic finding: with a threshold of 3, A-needs-B-and-C left only B, C unordered,
    and counting those two against the threshold silently dropped a real unordered pair
    that the old code reported. Ordering may only remove a finding, never raise the bar."""
    cfg = Config()
    cfg.loops.max_duplicate_items = 3
    _add(log, "A", globs=["x.py"], needs=["B", "C"])
    _add(log, "B", globs=["x.py"])
    _add(log, "C", globs=["x.py"])
    [f] = _dupes(log, cfg)
    assert f.item == "B" and f.count == 2 and "B, C" in f.detail, f


@pytest.mark.parametrize(
    "end", [("item.completed", {"sha": "x"}), ("task.removed", {}), ("item.abandoned", {})]
)
def test_a_link_that_is_done_removed_or_abandoned_orders_nothing(log, end):
    """B needs L, and L needs A. Once L is done or removed it holds B back no longer, and
    abandoned it holds B back forever: either way B's work does not follow A's. Each case
    stands alone, so a walk through the dead link would silence this pair."""
    _add(log, "A", globs=["x.py"])
    _add(log, "L", globs=["l.py"], needs=["A"])
    log.append(end[0], "L", end[1])
    _add(log, "B", globs=["x.py"], needs=["L"])
    [f] = _dupes(log)
    assert f.count == 2 and "A, B" in f.detail, f.detail


@pytest.mark.parametrize("dep", ["NO-SUCH-ITEM", "other-repo:A"])
def test_an_unknown_or_external_dependency_orders_nothing(log, dep):
    """`other-repo:A` is A in ANOTHER repository, not the local A."""
    _add(log, "A", globs=["x.py"])
    _add(log, "B", globs=["x.py"], needs=[dep])
    [f] = _dupes(log)
    assert f.count == 2 and "A, B" in f.detail, f.detail


def test_a_cycle_terminates_and_is_left_to_the_cycle_detector(log):
    _add(log, "A", globs=["x.py"], needs=["B"])
    _add(log, "B", globs=["x.py"], needs=["A"])
    evs = log.read_all()
    found = PR.detect(evs, fold(evs, strict=False), Config())
    assert [f.kind for f in found if f.kind == "dependency_cycle"], found
    # Each waits on the other, so neither can start beside the other: the cycle is the
    # finding, and a second one about shared files would add no remedy.
    assert not [f for f in found if f.kind == "duplicate_work"], found


@pytest.mark.parametrize("stack", [False, True])
def test_a_dependency_in_review_still_orders(log, stack):
    """In review, A's work is finished: without stacking B waits for the merge, with
    stacking B forks from A's branch. Either way nobody works A and B at the same time."""
    _add(log, "A", globs=["x.py"])
    log.append("pr.opened", "A", {"url": "https://example.invalid/pr/1", "number": 1})
    _add(log, "B", globs=["x.py"], needs=["A"])
    cfg = Config()
    cfg.flow.stack = stack
    assert fold(log.read_all(), strict=False).items["A"].state == "review"
    assert _dupes(log, cfg) == []


def test_doctor_no_longer_warns_about_an_ordered_pair(repo):
    assert run_cli(repo, "init")[0] == 0
    for argv in (
        ("task", "add", "K1", "--title", "fix", "--globs", "known.args"),
        ("task", "add", "K2", "--title", "deflake", "--globs", "known.args", "--needs", "K1"),
        ("task", "add", "D4", "--title", "metric", "--globs", "m.py"),
        ("task", "add", "D5", "--title", "anchor", "--globs", "m.py"),
    ):
        rc, out, err = run_cli(repo, *argv)
        assert rc == 0, (argv, out, err)
    _rc, out, err = run_cli(repo, "doctor")
    lines = [ln for ln in (out + err).splitlines() if "duplicate_work" in ln]
    assert len(lines) == 1 and "D4, D5" in lines[0], lines
