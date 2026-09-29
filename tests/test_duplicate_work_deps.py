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

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.config import Config
from ddflow.core import progress as PR
from ddflow.core.model import fold


def _add(log, iid: str, kind: str = "task", **data) -> None:
    log.append(f"{kind}.added", iid, {"title": iid, "kind": kind, **data})


def _dupes(log) -> list[PR.LoopFinding]:
    evs = log.read_all()
    found = PR.detect(evs, fold(evs, strict=False), Config())
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


def test_a_dependency_that_no_longer_waits_orders_nothing(log):
    """A done, removed or unknown link cannot hold B back, so A and B can run together."""
    _add(log, "A", globs=["x.py"])
    _add(log, "Done", globs=["d.py"], needs=["A"])
    log.append("item.completed", "Done", {"sha": "x"})
    _add(log, "Gone", globs=["g.py"], needs=["A"])
    log.append("task.removed", "Gone", {"reason": "test"})
    _add(log, "B1", globs=["x.py"], needs=["Done"])
    _add(log, "B2", globs=["x.py"], needs=["Gone"])
    _add(log, "B3", globs=["x.py"], needs=["NO-SUCH-ITEM", "other-repo:A"])
    [f] = _dupes(log)
    assert f.count == 4 and "A, B1, B2, B3" in f.detail, f.detail


def test_a_cycle_terminates_and_is_left_to_the_cycle_detector(log):
    _add(log, "A", globs=["x.py"], needs=["B"])
    _add(log, "B", globs=["x.py"], needs=["A"])
    evs = log.read_all()
    found = PR.detect(evs, fold(evs, strict=False), Config())
    assert [f.kind for f in found if f.kind == "dependency_cycle"], found


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
