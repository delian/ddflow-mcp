"""The ``rules`` export kind (B-export-rules): decisions by path, lessons by tag, workflow."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from ddflow.infra.log import EventLog
from ddflow.services.export import ExportError, Filters, query, registry
from ddflow.services.redact_report import redact_report

LAN = ".".join(("192", "168", "7", "41"))  # built, so no private host is committed text


def _repo(root: Path, order: str = "forward", with_extras: bool = True) -> query.Query:
    log = EventLog(root, "a1")
    evs = [
        (
            "decision.recorded",
            "D-store",
            {
                "title": "Storage is append-only",
                "decision": "Never rewrite a shard.",
                "globs": ["ddflow/infra/**", "ddflow/core/**"],
                "tags": ["storage"],
            },
        ),
        (
            "decision.recorded",
            "D-old",
            {"title": "Use sqlite as the log", "globs": ["ddflow/infra/**"]},
        ),
        (
            "decision.recorded",
            "D-new",
            {"title": "Replace sqlite", "globs": ["ddflow/infra/**"], "supersedes": ["D-old"]},
        ),
        ("decision.superseded", "D-old", {"by": "D-new"}),
        (
            "decision.recorded",
            "D-free",
            {"title": f"Review host is {LAN}", "decision": "Run it on the LAN."},
        ),
        (
            "decision.recorded",
            "D-rej",
            {"title": "Rejected idea", "status": "rejected", "globs": ["x/**"]},
        ),
        (
            "decision.recorded",
            "D-prop",
            {"title": "Maybe a cache", "status": "proposed", "globs": ["ddflow/api/**"]},
        ),
        (
            "lesson.recorded",
            "L-a",
            {
                "title": "Atomic writes",
                "rule": "Write then rename.\n\nSecond paragraph that is long.",
                "tags": ["io", "storage"],
            },
        ),
        (
            "lesson.recorded",
            "L-b",
            {
                "title": "Lock first",
                "rule": "ignored",
                "summary": "Take the lock before reading.",
                "tags": ["storage"],
            },
        ),
        ("lesson.recorded", "L-c", {"title": "No tag", "rule": "Plain rule."}),
        (
            "lesson.recorded",
            "L-d",
            {"title": "Old way", "rule": "Superseded rule.", "tags": ["io"]},
        ),
        (
            "lesson.recorded",
            "L-e",
            {"title": "New way", "rule": "Fresh.", "tags": ["io"], "supersedes": ["L-d"]},
        ),
    ]
    if order != "forward":
        # Reverse the independent records; a supersession is order-sensitive in the fold
        # by design (it needs its target first), so those stay last.
        def dependent(e) -> bool:
            return e[0] == "decision.superseded" or "supersedes" in e[2]

        evs = [e for e in evs[::-1] if not dependent(e)] + [e for e in evs if dependent(e)]
    for kind, sub, data in evs:
        log.append(kind, sub, data)
    return query.load(root)


def _body(q: query.Query, **f) -> str:
    return registry.render_body("rules", q, Filters(**f))


def test_registered_with_target_and_filters():
    k = registry.get("rules")
    assert k.default_target == "RULES.md" and k.update_mode == registry.WHOLE
    assert {"limit", "tag"} <= set(k.filters)


def test_superseded_decisions_and_lessons_excluded(tmp_path):
    body = _body(_repo(tmp_path))
    assert "D-old" not in body and "Use sqlite" not in body
    assert "L-d" not in body and "Old way" not in body
    assert "D-rej" not in body and "x/" not in body  # only accepted/proposed are in force
    assert "D-new" in body and "L-e" in body


def test_decisions_grouped_by_glob_and_pathless_listed_separately(tmp_path):
    body = _body(_repo(tmp_path))
    infra = body.index("### ddflow/infra/\\*\\*")
    core = body.index("### ddflow/core/\\*\\*")
    free = body.index("### No governing path")
    assert body.index("### ddflow/api/") < core < infra < free  # sorted by path, loose last
    # a decision appears under every path it governs
    assert body.count("`D-store`") == 2
    assert body.index("`D-free`") > free
    assert "`D-prop`" in body and "(proposed)" in body


def test_lessons_by_tag_summary_first_paragraph_once_each(tmp_path):
    body = _body(_repo(tmp_path))
    assert "Take the lock before reading." in body  # summary beats the rule
    assert "Write then rename." in body and "Second paragraph" not in body
    assert body.count("`L-a`") == 1  # listed under its first tag only
    assert body.index("### io") < body.index("`L-a`") < body.index("### storage")
    assert "### (untagged)" in body and "`L-c`" in body


def test_tag_filter_and_limit(tmp_path):
    q = _repo(tmp_path)
    t = _body(q, tag="storage")
    assert "`L-a`" in t and "`L-b`" in t and "`L-c`" not in t and "`L-e`" not in t
    assert "`D-store`" in t and "`D-free`" in t  # --tag narrows lessons, never decisions
    lim = _body(q, limit=2)
    assert "2 older lesson(s) not shown" in lim  # 4 live lessons, 2 kept
    assert "2 live lesson(s)" in lim and "`D-store`" in lim  # decisions are not limited


def test_deterministic_and_independent_of_write_order(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    qa, qb = _repo(a), _repo(b, order="reversed")
    assert _body(qa) == _body(qa)
    assert _body(qa) == _body(qb)


def test_workflow_section_from_the_config_in_force(tmp_path):
    body = _body(_repo(tmp_path))
    assert "## The enforced workflow" in body
    assert "1. `research`" in body and "`implement`" in body and "required" in body
    assert "A phase passes through:" in body
    assert "`gates.required` =" in body and "`enforce.commit_without_lease` =" in body
    assert "`schedule.max_parallel_tasks` =" in body
    # not machine state: no reviewer models, hook status or recording statistics
    assert "family=" not in body
    assert "hook" not in body.lower()


def test_in_memory_query_has_no_workflow_section(tmp_path):
    q = _repo(tmp_path)
    mem = query.build(q.events)
    assert mem.repo is None and "## The enforced workflow" not in _body(mem)


def test_unreadable_config_is_could_not_run_not_an_empty_section(tmp_path):
    q = _repo(tmp_path)
    (tmp_path / ".ddflow" / "config.toml").write_text("this is [not toml")
    with pytest.raises(ExportError) as e:
        _body(q)
    assert e.value.code == 2 and "workflow" in str(e.value)


def test_redactor_removes_a_lan_address_from_a_decision_title(tmp_path):
    doc = registry.render_document("rules", _repo(tmp_path), Filters())
    assert LAN in doc
    red = redact_report(doc)
    assert LAN not in red.text and red.total >= 1
    assert "[REDACTED:" in red.text


def test_big_log_is_summarised_and_fast(tmp_path):
    log = EventLog(tmp_path, "a1")
    for i in range(400):
        log.append(
            "lesson.recorded",
            f"L{i:04d}",
            {"title": f"lesson {i}", "rule": "x " * 400, "tags": [f"t{i % 9}"]},
        )
    q = query.load(tmp_path)
    t0 = time.perf_counter()
    full, capped = _body(q), _body(q, limit=20)
    assert time.perf_counter() - t0 < 5
    assert len(capped) < len(full) / 5 and "380 older lesson(s) not shown" in capped
