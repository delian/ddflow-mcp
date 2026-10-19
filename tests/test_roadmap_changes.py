"""The roadmap's Changed section, links and ETA hint (B-rm-changes, D-roadmap)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_export_docs_a import Log, _ts

from ddflow.config import Config
from ddflow.services.export import Filters, registry
from ddflow.services.export import roadmap_changes as CH


def _log() -> Log:
    g = Log()
    g.add("phase.added", "P1", {"title": "old", "priority": 10, "needs": []}, _ts(0))
    g.add("task.added", "T1", {"title": "early", "parent": "P1", "needs": []}, _ts(0))
    g.done("T1", _ts(1))
    g.add("task.added", "T2", {"title": "new work", "parent": "P1", "needs": []}, _ts(5))
    g.add("task.updated", "T2", {"priority": 99}, _ts(6))
    g.add("task.added", "T3", {"title": "stuck", "parent": "P1", "needs": []}, _ts(6))
    g.add("item.blocked", "T3", {"reason": "waiting"}, _ts(7))
    g.add("task.added", "T4", {"title": "hidden", "parent": "P1", "needs": []}, _ts(7))
    g.done("T2", _ts(8))
    return g


def _ids(section: dict) -> list[str]:
    return [r["id"] for r in section["items"]]


def test_no_point_in_time_gives_no_section():
    assert CH.changed(_log().query(), "", lambda _i: True) == {}


def test_changes_since_a_date_are_sorted_into_the_four_kinds():
    ch = CH.changed(_log().query(), "2026-09-04", lambda i: i != "T4")
    assert ch["since"] == "2026-09-04"
    assert _ids(ch["added"]) == ["T2", "T3"] and _ids(ch["completed"]) == ["T2"]
    assert _ids(ch["blocked"]) == ["T3"] and _ids(ch["reprioritised"]) == ["T2"]
    assert ch["reprioritised"]["items"][0]["priority"] == 99
    assert "T1" not in str(ch)  # completed before the point: not a change


def test_a_long_list_is_cut_and_counted():
    ch = CH.changed(_log().query(), "2026-09-01", lambda _i: True, cap=1)
    assert len(ch["added"]["items"]) == 1 and ch["added"]["more"] >= 2


def test_decisions_by_glob_overlap_and_research_by_item():
    g = _log()
    g.add("task.updated", "T2", {"globs": ["src/a/**"]}, _ts(5))
    g.add("decision.recorded", "D-a", {"title": "A", "globs": ["src/a/x.py"], "status": "accepted"})
    g.add("decision.recorded", "D-z", {"title": "Z", "globs": ["docs/**"], "status": "accepted"})
    g.add("research.recorded", "R1", {"item": "T2", "question": "q", "claim": "c"})
    out = CH.links(g.query(), {"P1": ["P1", "T1", "T2"]})["P1"]
    assert out["decisions"] == ["D-a"] and out["research"] == ["R1"]
    assert out["more_decisions"] == 0 and out["more_research"] == 0


def test_the_eta_is_absent_when_the_forecast_has_too_little_data():
    q = _log().query()
    assert CH.etas(q, Config(), ["P1"]) == {}
    assert CH.etas(q, Config(), []) == {}


def test_spans_read_short():
    assert (
        CH.hours(600) == "10 min"
        and CH.hours(7200) == "2.0 h"
        and CH.hours(3 * 86400) == "3.0 days"
    )


def _g() -> Log:
    g = _log()
    g.add("task.updated", "T2", {"globs": ["src/a/**"]}, _ts(5))
    g.add("decision.recorded", "D-a", {"title": "A", "globs": ["src/a/x.py"], "status": "accepted"})
    g.add("research.recorded", "R1", {"item": "T2", "question": "q", "claim": "c"})
    return g


def test_the_document_has_a_changed_section_and_links():
    body = registry.render_body("roadmap", _g().query(), Filters(since="2026-09-04"))
    assert "## Changed since 2026-09-04" in body and "### Added" in body
    assert "`T3` stuck" in body and "(priority 99)" in body
    assert "Governed by D-a." in body and "Research: R1." in body


def test_no_since_and_no_tag_means_no_changed_section():
    body = registry.render_body("roadmap", _g().query(), Filters())
    assert "Changed since" not in body


def test_the_header_records_since():
    doc = registry.render_document("roadmap", _g().query(), Filters(since="2026-09-04"))
    assert " since=2026-09-04 " in doc.splitlines()[0]
    assert (
        "since=" not in registry.render_document("roadmap", _g().query(), Filters()).splitlines()[0]
    )


def test_an_eta_is_printed_as_an_estimate_only_when_the_forecast_has_a_range(monkeypatch):
    q = _g().query()
    assert "ETA (estimate)" not in registry.render_body("roadmap", q, Filters())
    monkeypatch.setattr(
        CH, "etas", lambda _q, _c, ids: {i: {"low_s": 7200, "high_s": 3 * 86400} for i in ids}
    )
    body = registry.render_body("roadmap", q, Filters())
    assert "ETA (estimate): 2.0 h to 3.0 days." in body


def test_research_has_no_globs_so_it_links_by_item_only():
    from dataclasses import fields

    from ddflow.core.records import ResearchNote

    assert "globs" not in {f.name for f in fields(ResearchNote)}
    assert CH.eta_line({"low_s": 7200, "high_s": 3 * 86400}) == "2.0 h to 3.0 days"
