"""Roadmap data v2 (B-rm-data, D-roadmap): order, lanes by commitment, counts, cap, hiding."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_export_docs_a import Log, _body, _ts, road_log

from ddflow.services.export import Filters, registry
from ddflow.services.export import roadmap_calc as RC


def data(log: Log, **f) -> dict:
    return registry.get("roadmap").data(log.query(), Filters(**f))


def phases(d: dict) -> dict[str, dict]:
    return {p["id"]: p for lane in d["lanes"] for p in lane["phases"]}


def test_percent_and_status_counts_per_phase():
    p = phases(data(road_log()))["P1"]
    assert (p["done"], p["total"], p["percent"]) == (1, 3, 33)
    assert p["counts"] == {"running": 1, "blocked": 1, "done": 1}
    assert RC.percent(0, 0) == 100


def test_in_flight_is_now_and_a_waiting_phase_is_later_with_its_reason():
    d = data(road_log())
    lanes = {lane["label"]: [p["id"] for p in lane["phases"]] for lane in d["lanes"]}
    assert lanes["Now"] == ["P1"] and "P2" in lanes["Next"]
    p3 = phases(d)["P3"]
    assert p3["blocked_by"] == ["P2"] and p3["tasks"] == [] and p3["more_tasks"] == 1


def test_next_is_the_top_n_ready_phases_the_rest_wait_in_later():
    g = Log()
    for i in range(1, 6):
        g.phase(f"R{i}", f"Ready {i}", i * 10)
        g.task(f"RT{i}", f"R{i}", "work")
    d = data(g)
    lanes = {lane["label"]: lane["phases"] for lane in d["lanes"]}
    assert [p["id"] for p in lanes["Next"]] == ["R1", "R2", "R3"]
    assert [p["id"] for p in lanes["Later"]] == ["R4", "R5"]
    assert all(p["ready"] for p in lanes["Later"])


def test_phases_follow_dependency_order_then_priority():
    g = Log()
    g.phase("A", "late but first", 90)
    g.phase("B", "early but waits", 10, needs=["A"])
    g.phase("C", "no deps", 50)
    order = RC.order(["A", "B", "C"], lambda i: {"B": ["A"]}.get(i, []), lambda i: (1, i))
    assert order.index("A") < order.index("B")
    assert RC.order(["B", "A"], lambda i: {"A": ["B"], "B": ["A"]}[i], str) == ["A", "B"]  # cycle


def test_task_list_is_capped_in_flight_first_and_counted():
    g = Log()
    g.phase("P", "Big", 10)
    for i in range(12):
        g.task(f"T{i:02d}", "P", f"task {i}")
    g.run("T11", _ts(1))
    p = phases(data(g))["P"]
    assert len(p["tasks"]) == 8 and p["more_tasks"] == 4
    assert p["tasks"][0]["id"] == "T11"
    assert "4 more open tasks not listed" in _body("roadmap", g.query())


def test_bl_batches_are_hidden_by_default_and_counted():
    g = Log()
    g.phase("BL-batch", "internal", 5)
    g.task("BT", "BL-batch", "x")
    g.phase("P", "real", 10)
    g.task("T", "P", "y")
    d = data(g)
    assert "BL-batch" not in phases(d) and d["hidden_batches"] == 1
    assert "1 internal batches hidden" in _body("roadmap", g.query())
    assert RC.hidden("X", ["internal"], ["internal"]) and not RC.hidden("X", [], ["BL-"])


def test_critical_path_is_named_and_marks_its_phases():
    d = data(road_log())
    chain = [s["id"] for s in d["critical_path"]]
    assert len(chain) >= 2
    ph = phases(d)
    on = {pid for pid, p in ph.items() if p["on_critical_path"]}
    expect = {pid for pid, p in ph.items() if any(t["id"] in chain for t in p["tasks"])}
    assert on and on >= expect and len(on) < len(ph)


def test_release_lines_group_phases_only_when_there_are_several():
    g = Log()
    g.add("phase.added", "P1", {"title": "old", "priority": 10, "needs": [], "line": "1.x"})
    g.task("T1", "P1", "a")
    g.add("phase.added", "P2", {"title": "new", "priority": 20, "needs": [], "line": "2.x"})
    g.task("T2", "P2", "b")
    assert data(road_log())["release_lines"] == []
    assert [x["line"] for x in data(g)["release_lines"]] == ["1.x", "2.x"]
    lines = RC.by_line([{"id": "a", "line": "1.x", "done": 1, "total": 2},
                        {"id": "b", "line": "2.x", "done": 0, "total": 1}])  # fmt: skip
    assert [(x["line"], x["percent"]) for x in lines] == [("1.x", 50), ("2.x", 0)]


def test_schema_version_is_bumped_and_limit_still_counts():
    assert registry.get("roadmap").schema_version == 2
    d = data(road_log(), limit=1)
    assert d["hidden_phases"] >= 0 and d["open_tasks"] == 5
