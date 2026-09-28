"""Bug fixes are offered before features unless the operator says otherwise.

The operator's words (2026-09-28): "prioritize the fixing of the bugs by default in front
of features so the bugs are not affecting the feature development". Ordering the ready set
by `priority` alone put a bug filed at the default 100 behind any feature filed at 10, and
with one free slot the bug was not offered at all.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ddflow.core.model import fold
from ddflow.core.schedule import plan


def build(log, spec):
    """spec: (id, priority, tags) per task, each on its own files so none conflict."""
    log.append("phase.added", "P1", {"title": "phase"})
    for tid, priority, tags in spec:
        log.append(
            "task.added",
            tid,
            {"parent": "P1", "globs": [f"{tid}/*"], "priority": priority, "tags": tags},
        )
    return fold(log.read_all())


def ready(st, cfg):
    return [i.id for i in plan(st, cfg, phase="P1").ready]


def test_a_tagged_bug_is_offered_before_a_higher_priority_feature(log, cfg):
    st = build(log, [("FEAT", 10, []), ("FIX", 100, ["bug"]), ("HOT", 100, ["hotfix"])])
    assert ready(st, cfg) == ["FIX", "HOT", "FEAT"]


def test_priority_still_orders_bugs_among_themselves_and_features_among_themselves(log, cfg):
    st = build(
        log,
        [("F2", 50, []), ("F1", 10, []), ("B2", 100, ["bugfix"]), ("B1", 20, ["fix"])],
    )
    assert ready(st, cfg) == ["B1", "B2", "F1", "F2"]


def test_the_parallelism_cap_hands_its_slot_to_the_bug(log, cfg):
    st = build(log, [("FEAT", 10, []), ("FIX", 100, ["bug"])])
    cfg.schedule.max_parallel_tasks = 1
    p = plan(st, cfg, phase="P1")
    assert [i.id for i in p.ready] == ["FIX"]
    assert [b.item for b in p.blocked] == ["FEAT"]


def test_an_open_bug_record_promotes_the_item_it_names(log, cfg):
    log.append("bug.found", "Bx", {"item": "T2", "summary": "broken"})
    st = build(log, [("T1", 10, []), ("T2", 100, [])])
    assert ready(st, cfg) == ["T2", "T1"]


def test_a_fixed_bug_record_no_longer_promotes_its_item(log, cfg):
    log.append("bug.found", "Bx", {"item": "T2", "summary": "broken"})
    log.append("bug.fixed", "Bx", {"regression_test": "tests/x.py"})
    st = build(log, [("T1", 10, []), ("T2", 100, [])])
    assert ready(st, cfg) == ["T1", "T2"]


def test_the_operator_can_turn_it_off(log, cfg):
    st = build(log, [("FEAT", 10, []), ("FIX", 100, ["bug"])])
    cfg.schedule.bugs_first = False
    assert ready(st, cfg) == ["FEAT", "FIX"]
