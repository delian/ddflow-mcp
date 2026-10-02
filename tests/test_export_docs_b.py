"""Export kinds (B-export-worklog-sessions): worklog, sessions, decisions index.

Built from a small synthetic log of Events with explicit timestamps (no clock, no project
data), so each data trap the research found is one visible fixture row.
"""

from __future__ import annotations

import pytest

from ddflow.core.events import Event
from ddflow.services.export import EXIT_REFUSED, ExportError, Filters, query, registry

AG = "agent-a"
SA = "sess-agent"  # the agent that ran the sessions


def _events(extra: list[Event] | None = None) -> list[Event]:
    seq = [0]

    def ev(kind: str, subject: str, ts: str, agent: str = AG, **data: object) -> Event:
        seq[0] += 1
        return Event(kind, subject, data, agent=agent, lamport=seq[0], ts=ts)

    out = [
        ev("phase.added", "P1", "2026-09-01T08:00:00Z", title="Phase one"),
        ev(
            "task.added",
            "T-fix",
            "2026-09-01T08:01:00Z",
            title="Repair the thing (fixes bug B-x)",
            parent="P1",
        ),
        ev("task.added", "T-feat", "2026-09-01T08:02:00Z", title="Add the feature", parent="P1"),
        ev(
            "bug.found", "B-x", "2026-09-01T08:03:00Z", summary="The thing is broken", item="T-feat"
        ),
        # one item, one afternoon: merged, completed and its bug fixed -> ONE line
        ev("worktree.merged", "T-fix", "2026-09-20T15:00:00Z", sha="abc"),
        ev("item.completed", "T-fix", "2026-09-20T15:01:00Z"),
        ev("bug.fixed", "B-x", "2026-09-20T15:02:00Z", regression_test="tests/t.py::t"),
        ev(
            "research.recorded",
            "R-q",
            "2026-09-20T16:00:00Z",
            question="Does it work?",
            claim="yes",
        ),
        ev("research.recorded", "R-untitled", "2026-09-20T16:01:00Z"),
        ev(
            "decision.recorded",
            "D-one",
            "2026-09-21T09:00:00Z",
            title="Use one thing",
            globs=["src/**"],
        ),
        ev(
            "decision.recorded",
            "D-two",
            "2026-09-22T09:00:00Z",
            title="Use another | thing",
            globs=["a/**", "b/**", "c/**", "d/**"],
        ),
        ev("decision.recorded", "D-three", "2026-09-23T09:00:00Z", title="Old idea"),
        ev("decision.superseded", "D-three", "2026-09-24T09:00:00Z", by="D-two"),
        # imported journal notes: ALL stamped with one import day, but dated by data.at
        ev(
            "session.note",
            "s-import",
            "2026-09-25T10:00:00Z",
            text="journal entry from May",
            at="2026-05-03",
            source="docs/LOG.md:10",
        ),
        ev(
            "session.note",
            "s-import",
            "2026-09-25T10:00:01Z",
            text="journal entry from July",
            at="2026-07-14T12:00:00Z",
            source="docs/LOG.md:55",
        ),
        ev(
            "session.note",
            "s-import",
            "2026-09-25T10:00:02Z",
            text="note with no origin date",
            source="docs/LOG.md:90",
        ),
        ev(
            "session.note",
            "s-live",
            "2026-09-26T10:00:00Z",
            SA,
            text="a live agent note stays out of the log",
        ),
        # sessions
        ev("session.started", "s-live", "2026-09-26T09:00:00Z", SA, model="claude"),
        ev(
            "session.prompt",
            "s-live",
            "2026-09-26T09:01:00Z",
            SA,
            text="please look at the roadmap",
            item="",
        ),
        ev(
            "session.prompt",
            "s-live",
            "2026-09-26T09:02:00Z",
            SA,
            text="You are a subagent implementing ONE task. " + "x" * 50,
        ),
        ev("item.started", "T-feat", "2026-09-26T09:05:00Z", SA),
        ev(
            "session.ended",
            "s-live",
            "2026-09-26T11:00:00Z",
            SA,
            summary="Built the feature; handoff: docs still open.",
        ),
        ev("session.started", "s-quiet", "2026-09-27T09:00:00Z", SA, model="claude"),
        ev("session.ended", "s-quiet", "2026-09-27T09:30:00Z", SA, summary=""),
    ]
    return out + (extra or [])


@pytest.fixture
def q() -> query.Query:
    return query.build(_events())


def _body(kind: str, q: query.Query, **f) -> str:
    return registry.render_body(kind, q, Filters(**f))


def test_kinds_are_registered_with_default_targets():
    assert "worklog" in registry.names() and "sessions" in registry.names()
    assert "decisions" in registry.names()
    assert registry.get("worklog").default_target == "LOG.md"
    assert registry.get("sessions").default_target == "SESSION.md"
    assert registry.get("decisions").default_target == "DECISIONS.md"


# -- worklog ----------------------------------------------------------------------------


def test_worklog_coalesces_one_item_into_one_line(q):
    body = _body("worklog", q, since="2026-09-20")
    day = body.split("## 2026-09-20")[1].split("\n## ")[0]
    lines = [ln for ln in day.splitlines() if "`T-fix`" in ln]
    assert len(lines) == 1
    assert "**merged, completed, bug fixed**" in lines[0]
    # the fixed bug rode on the item's line instead of getting its own
    assert not any(
        ln.startswith("- ") and "`B-x`" in ln.split("`T-fix`")[0] for ln in day.splitlines()
    )
    assert sum(1 for ln in day.splitlines() if ln.startswith("- ") and "bug fixed" in ln) == 1


def test_worklog_research_line_carries_its_title(q):
    body = _body("worklog", q, since="2026-09-20")
    assert "`R-q` Does it work?" in body
    # an unknown title is empty, never a crash or "None"
    assert "`R-untitled`" in body and "None" not in body


def test_worklog_groups_imported_notes_by_data_at_not_import_ts(q):
    body = _body("worklog", q, since="2026-01-01")
    may = body.split("## 2026-05-03")[1].split("\n## ")[0]
    july = body.split("## 2026-07-14")[1].split("\n## ")[0]
    assert "journal entry from May" in may and "[source](docs/LOG.md:10)" in may
    assert "journal entry from July" in july
    # a note without data.at falls back to its event ts (the import day)
    sept = body.split("## 2026-09-25")[1].split("\n## ")[0]
    assert "note with no origin date" in sept
    # none of the dated notes landed on the import day
    assert "journal entry from May" not in sept and "journal entry from July" not in sept


def test_worklog_import_day_spreads_by_month():
    """1,758 notes sharing one import day must still spread over their own months."""
    evs = [
        Event(
            "session.note",
            "s-imp",
            {"text": f"note {i}", "at": f"2025-{1 + i % 12:02d}-10", "source": f"j.md:{i}"},
            agent=AG,
            lamport=i + 1,
            ts="2026-09-25T10:00:00Z",
        )
        for i in range(1758)
    ]
    body = registry.render_body("worklog", query.build(evs), Filters(since="2025-01-01"))
    days = [ln for ln in body.splitlines() if ln.startswith("## ")]
    assert len(days) == 12
    assert "## 2026-09-25" not in body


def test_worklog_is_bounded_without_since_and_says_so(q):
    body = _body("worklog", q)
    assert "Showing the last 30 days" in body
    assert "journal entry from May" not in body  # outside the window by data.at
    assert "## 2026-09-20" in body


def test_worklog_live_agent_notes_are_not_in_the_log(q):
    assert "a live agent note" not in _body("worklog", q, since="2026-01-01")


def test_worklog_limit_keeps_the_newest(q):
    body = _body("worklog", q, since="2026-01-01", limit=2)
    assert "2 entries" in body
    assert "journal entry from May" not in body


def test_worklog_bad_since_is_refused(q):
    with pytest.raises(ExportError):
        _body("worklog", q, since="not-a-date")


@pytest.mark.parametrize("kind", ["worklog", "sessions", "decisions"])
def test_documents_are_byte_identical_twice_from_two_loads(kind):
    f = Filters(since="2026-01-01") if kind != "sessions" else Filters()
    a = registry.render_document(kind, query.build(_events()), f)
    b = registry.render_document(kind, query.build(_events()), f)
    assert a == b


# -- sessions ---------------------------------------------------------------------------


def test_sessions_summary_comes_first_and_brief_is_labelled():
    q = query.build(_events())
    body = _body("sessions", q, session="s-live")
    section = body.split("## s-live")[1]
    assert section.index("**Summary and handoff.**") < section.index("Prompts:")
    assert "Built the feature; handoff: docs still open." in section
    assert "[operator] please look at the roadmap" in section
    assert "[brief] You are a subagent" in section
    assert "`T-feat`" in section  # items touched, found by agent + time window
    assert "model claude" in section


def test_sessions_without_a_summary_say_so_and_filters_apply():
    q = query.build(_events())
    body = _body("sessions", q)
    assert "**Summary and handoff.** none recorded." in body.split("## s-quiet")[1]
    assert "1 carry a summary" in body
    assert "## s-live" in body and "## s-quiet" in body
    assert body.index("## s-quiet") < body.index("## s-live")  # newest first
    assert "## s-live" not in _body("sessions", q, limit=1)
    assert "## s-quiet" not in _body("sessions", q, session="s-live")


def test_sessions_do_not_copy_imported_notes():
    q = query.build(_events())
    body = _body("sessions", q, session="s-import")
    assert "journal entry from May" not in body
    assert "3 imported journal notes" in body


def test_sessions_refuses_a_filter_it_does_not_take(q):
    with pytest.raises(ExportError) as e:
        _body("sessions", q, status="open")
    assert e.value.code == EXIT_REFUSED


# -- decisions --------------------------------------------------------------------------


def test_decisions_table_has_status_date_title_governs(q):
    body = _body("decisions", q)
    assert "| `D-one` | accepted | 2026-09-21 | Use one thing | src/** |" in body
    assert "| `D-three` | superseded by D-two | 2026-09-23 | Old idea | - |" in body
    # a pipe in a title cannot break the table; governs is capped
    assert "Use another \\| thing" in body
    assert "a/**, b/**, c/** ..." in body
    assert "3 of 3" in body or "3 decisions" in body


def test_decisions_status_filter(q):
    body = _body("decisions", q, status="superseded")
    assert "`D-three`" in body and "`D-one`" not in body


def test_decisions_newest_first(q):
    body = _body("decisions", q)
    assert body.index("`D-two`") < body.index("`D-one`")


def test_worklog_limit_keeps_the_newest_lines_inside_a_day():
    def note(i: int, hh: str) -> Event:
        return Event(
            "item.completed", f"T-{i}", {}, agent=AG, lamport=i, ts=f"2026-09-01T{hh}:00:00Z"
        )

    q = query.build([note(1, "09"), note(2, "12"), note(3, "17")])
    body = registry.render_body("worklog", q, Filters(since="2026-09-01", limit=1))
    assert "`T-3`" in body and "`T-1`" not in body and "`T-2`" not in body


def test_session_without_started_at_uses_its_first_prompt_and_does_not_crash():
    evs = [
        Event(
            "session.prompt",
            "s-x",
            {"text": "hello"},
            agent=SA,
            lamport=1,
            ts="2026-09-01T09:00:00Z",
        ),
        Event("item.started", "T-1", {}, agent=SA, lamport=2, ts="2026-09-01T09:05:00Z"),
        Event(
            "task.added",
            "T-1",
            {"title": "t", "parent": ""},
            agent=SA,
            lamport=0,
            ts="2026-09-01T08:00:00Z",
        ),
    ]
    body = registry.render_body("sessions", query.build(evs), Filters())
    assert "`T-1`" in body and "hello" in body


def test_decisions_live_count_is_of_the_whole_set_not_the_page(q):
    body = registry.render_body("decisions", q, Filters(limit=1))
    assert "1 of 3 decisions; 2 accepted in all" in body


def test_worklog_limit_across_days_is_the_newest_n_in_order():
    evs = [
        Event("item.completed", f"T-{i}", {}, agent=AG, lamport=i, ts=ts)
        for i, ts in enumerate(
            ["2026-09-01T09:00:00Z", "2026-09-02T10:00:00Z", "2026-09-02T11:00:00Z"], 1
        )
    ]
    body = registry.render_body("worklog", query.build(evs), Filters(since="2026-09-01", limit=2))
    assert "`T-1`" not in body
    assert body.index("`T-2`") < body.index("`T-3`")
    assert "## 2026-09-01" not in body


def test_a_session_with_no_start_and_no_prompt_claims_no_items_by_agent():
    evs = [
        Event("session.note", "s-y", {"text": "n"}, agent=SA, lamport=1, ts="2026-09-01T09:00:00Z"),
        Event(
            "task.added",
            "T-1",
            {"title": "t", "parent": ""},
            agent=SA,
            lamport=0,
            ts="2026-09-01T08:00:00Z",
        ),
        Event("item.started", "T-1", {}, agent=SA, lamport=2, ts="2026-09-01T09:05:00Z"),
    ]
    body = registry.render_body("sessions", query.build(evs), Filters())
    assert "items: none recorded" in body
