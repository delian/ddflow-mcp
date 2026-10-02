"""`show` and `brief` display what was added to a record and what links to it
(B-addenda-render, decision D-no-duplicates): additions verbatim with who/when/score, links
in both directions, a bug's fixing task, and -- for the agent holding an item -- how many
reports arrived since it claimed."""

from __future__ import annotations

import json
import re
import time

import pytest
from conftest import run_cli

from ddflow.infra.log import EventLog

OK = 0
ADD1 = "Login also fails when the password has a trailing space."
ADD2 = "Second addition:\nwith two lines, and a unicode dash — kept."
FILED = "Login rejects passwords that end in whitespace, every time."


@pytest.fixture
def proj(repo):
    assert run_cli(repo, "init")[0] == OK
    run_cli(repo, "phase", "add", "P1", "--title", "Auth")
    run_cli(
        repo, "task", "add", "P1.T1", "--phase", "P1", "--title", "login", "--globs", "src/auth/*"
    )
    return repo


def _log(repo):
    return EventLog(repo, "reporter")


def _addition(repo, record, text, score=0.61):
    time.sleep(0.01)  # past the claim's timestamp on any clock granularity
    _log(repo).append("record.extended", record, {"text": text, "who": "reporter", "score": score})


def test_show_a_bug_prints_additions_links_and_the_fixing_task(proj):
    log = _log(proj)
    log.append("bug.found", "B1", {"item": "", "summary": "login is broken"})
    _addition(proj, "B1", ADD1)
    log.append("bug.found", "B2", {"item": "", "summary": FILED, "extends": "B1", "score": 0.7})
    run_cli(
        proj,
        "task",
        "add",
        "P1.FIX",
        "--phase",
        "P1",
        "--title",
        "fix login (fixes bug B1)",
        "--globs",
        "src/fix/*",
    )
    code, out, _ = run_cli(proj, "show", "B1")
    assert code == OK
    assert "additions (1)" in out and ADD1 in out and "by reporter" in out and "0.61" in out
    assert re.search(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\S* by reporter", out)
    assert "fix task(s): P1.FIX" in out
    assert "linked from" in out and "B2" in out and "extends this" in out
    # ... and the other direction, from the record that was filed
    out2 = run_cli(proj, "show", "B2")[1]
    assert "links:" in out2 and "extends B1" in out2


def test_show_json_carries_additions_and_inbound_links(proj):
    log = _log(proj)
    _addition(proj, "P1.T1", ADD2)
    log.append("bug.found", "B2", {"item": "", "summary": FILED, "duplicate_of": "P1.T1"})
    code, out, _ = run_cli(proj, "--json", "show", "P1.T1")
    d = json.loads(out)
    assert code == OK
    assert d["additions"][0]["text"] == ADD2 and d["additions"][0]["who"] == "reporter"
    assert d["additions"][0]["score"] == 0.61 and d["additions"][0]["at"]
    assert [x["record"] for x in d["linked_from"]] == ["B2"]
    assert d["linked_from"][0]["relation"] == "duplicate_of"


def test_related_back_link_is_not_listed_twice(proj):
    log = _log(proj)
    log.append("bug.found", "B2", {"item": "", "summary": FILED, "related": "P1.T1"})
    log.append("link.recorded", "P1.T1", {"relation": "related", "target": "B2"})
    d = json.loads(run_cli(proj, "--json", "show", "P1.T1")[1])
    assert [x["target"] for x in d["links"]] == ["B2"]
    assert d["linked_from"] == []


def _claimed_with_reports(proj):
    _addition(proj, "P1.T1", "added BEFORE the claim")
    assert run_cli(proj, "claim", "P1.T1", "--no-worktree", agent="a")[0] == OK
    _addition(proj, "P1.T1", ADD1)
    _log(proj).append(
        "bug.found", "B2", {"item": "", "summary": FILED, "extends": "P1.T1", "score": 0.7}
    )


def test_brief_for_a_claimed_item_leads_with_the_count_and_the_reports(proj):
    _claimed_with_reports(proj)
    code, out, _ = run_cli(proj, "--json", "brief", "--item", "P1.T1", agent="a")
    text = json.loads(out)["brief"]
    assert code == OK
    assert text.startswith("## 2 new reports on your item since you claimed")
    assert ADD1 in text and FILED in text and "B2" in text
    assert "added BEFORE the claim" not in text


def test_brief_without_new_reports_has_no_such_heading(proj):
    run_cli(proj, "claim", "P1.T1", "--no-worktree", agent="a")
    text = json.loads(run_cli(proj, "--json", "brief", "--item", "P1.T1", agent="a")[1])["brief"]
    assert "new report" not in text


def test_brief_with_many_reports_stays_within_its_budget(proj):
    run_cli(proj, "claim", "P1.T1", "--no-worktree", agent="a")
    for i in range(30):
        _addition(proj, "P1.T1", f"report {i} " + "padding " * 200)
    for i in range(40):
        assert (
            run_cli(
                proj,
                "lesson",
                "add",
                "--title",
                f"Lesson {i} " + "padding " * 40,
                "--rule",
                "x " * 200,
            )[0]
            == OK
        )
    out = run_cli(proj, "--json", "brief", "--item", "P1.T1", agent="a")[1]
    data = json.loads(out)
    assert data["brief"].startswith("## 30 new reports on your item")
    assert "more: `ddflow show P1.T1`" in data["brief"]
    assert data["approx_tokens"] <= 1200 * 1.1


def test_heartbeat_and_gate_status_mention_the_count(proj):
    _claimed_with_reports(proj)
    hb = run_cli(proj, "heartbeat", "P1.T1", agent="a")[1]
    assert "2 new reports on P1.T1 since you claimed" in hb
    gs = run_cli(proj, "gate", "status", "P1.T1", agent="a")[1]
    assert "2 new reports on P1.T1 since you claimed" in gs


def test_a_related_record_filed_after_the_claim_is_counted(proj):
    run_cli(proj, "claim", "P1.T1", "--no-worktree", agent="a")
    log = _log(proj)
    log.append("bug.found", "B2", {"item": "", "summary": FILED, "related": "P1.T1"})
    log.append("link.recorded", "P1.T1", {"relation": "related", "target": "B2"})
    text = json.loads(run_cli(proj, "--json", "brief", "--item", "P1.T1", agent="a")[1])["brief"]
    assert text.startswith("## 1 new report on your item since you claimed")
    assert "B2" in text


def test_the_reports_block_is_clipped_to_a_small_budget(proj):
    run_cli(proj, "claim", "P1.T1", "--no-worktree", agent="a")
    for i in range(5):
        _addition(proj, "P1.T1", f"report {i} " + "padding " * 100)
    (proj / ".ddflow" / "config.toml").write_text("[session]\nbrief_max_tokens = 300\n")
    data = json.loads(run_cli(proj, "--json", "brief", "--item", "P1.T1", agent="a")[1])
    assert data["brief"].startswith("## 5 new reports")
    assert data["approx_tokens"] <= 300 * 1.1


def test_a_lesson_or_decision_linked_to_a_claimed_item_renders_in_the_brief(proj):
    """`Lesson.text` and `Decision.text` are methods: the brief must quote their text."""
    run_cli(proj, "claim", "P1.T1", "--no-worktree", agent="a")
    log = _log(proj)
    log.append(
        "lesson.recorded",
        "L1",
        {"title": "Whitespace passwords", "rule": "Trim nothing.", "extends": "P1.T1"},
    )
    log.append(
        "decision.recorded",
        "D1",
        {"title": "Keep spaces", "decision": "Never trim.", "extends": "P1.T1"},
    )
    code, out, err = run_cli(proj, "--json", "brief", "--item", "P1.T1", agent="a")
    assert code == OK, err
    text = json.loads(out)["brief"]
    assert text.startswith("## 2 new reports") and "Trim nothing." in text
    assert "Never trim." in text
    assert OK == run_cli(proj, "show", "P1.T1")[0]


def test_a_related_link_made_at_add_time_on_the_item_is_not_a_report_on_it(proj):
    """An add-time `related` link on the item is the item's own link, not a report."""
    from ddflow.api.reporting import new_reports
    from ddflow.core.model import fold

    log = _log(proj)
    log.append("bug.found", "B2", {"item": "", "summary": FILED})
    log.append("task.added", "P1.T3", {"title": "t3", "body": "", "related": "B2"})
    st = fold(log.read_all(), strict=False)
    assert [x["target"] for x in st.links["P1.T3"].links] == ["B2"]
    assert new_reports(st, "P1.T3", 0.0)["count"] == 0
    log.append("link.recorded", "P1.T3", {"relation": "related", "target": "P1.T4"})
    st = fold(log.read_all(), strict=False)
    assert new_reports(st, "P1.T3", 0.0)["count"] == 1
