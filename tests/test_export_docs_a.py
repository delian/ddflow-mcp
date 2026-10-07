"""Export kinds roadmap, bugs and status (B-export-docs-bugs-roadmap-status).

Every log here is built at test time from synthetic events with fixed timestamps.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

import pytest

from ddflow.core.events import Event
from ddflow.core.progress import epoch
from ddflow.services.export import EXIT_REFUSED, ExportError, Filters, frame, query, registry

T0 = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)


def _ts(days: float = 0, hours: float = 0) -> str:
    return (T0 + timedelta(days=days, hours=hours)).isoformat(timespec="microseconds")


class Log:
    """Events with fixed timestamps and sequential lamports."""

    def __init__(self) -> None:
        self.events: list[Event] = []

    def add(self, kind: str, subject: str, data: dict | None = None, ts: str = "") -> None:
        self.events.append(
            Event(kind, subject, data or {}, "a1", len(self.events) + 1, ts or _ts(0))
        )

    def phase(self, pid: str, title: str, prio: int, needs: list[str] | None = None) -> None:
        self.add("phase.added", pid, {"title": title, "priority": prio, "needs": needs or []})

    def task(self, tid: str, parent: str, title: str, needs: list[str] | None = None) -> None:
        self.add("task.added", tid, {"title": title, "parent": parent, "needs": needs or []})

    def done(self, tid: str, ts: str = "") -> None:
        self.add("item.completed", tid, {}, ts)

    def run(self, tid: str, ts: str) -> None:
        self.add("lease.acquired", tid, {"holder": "w1", "at": epoch(ts)}, ts)
        self.add("item.started", tid, {}, ts)

    def bug(self, bid: str, summary: str, day: float, item: str = "") -> None:
        self.add("bug.found", bid, {"summary": summary, "item": item}, _ts(day))

    def fixed(self, bid: str, day: float, test: str = "tests/test_x.py::test_y") -> None:
        self.add("bug.fixed", bid, {"regression_test": test}, _ts(day))

    def invalid(self, bid: str, day: float, reason: str) -> None:
        self.add("bug.invalid", bid, {"reason": reason}, _ts(day))

    def query(self, reverse: bool = False) -> query.Query:
        q = query.build(sorted(self.events, key=lambda e: e.sort_key()))
        if reverse:  # the same state with every dict in the opposite fold order
            st = q.state
            st.items = dict(reversed(st.items.items()))
            st.bugs = dict(reversed(st.bugs.items()))
            q = query.Query(st, q.events)
        return q


def road_log() -> Log:
    g = Log()
    g.phase("P1", "First phase", 10)
    g.task("T1", "P1", "done thing")
    g.task("T2", "P1", "running thing")
    g.task("T3", "P1", "waits on running", needs=["T2"])
    g.done("T1", _ts(1))
    g.run("T2", _ts(2))
    g.phase("P2", "Second phase", 20)
    g.task("T4", "P2", "ready thing")
    g.phase("P3", "Third phase", 30, needs=["P2"])
    g.task("T5", "P3", "inherits the phase dependency")
    g.phase("P4", "Fourth phase", 40)
    g.task("T6", "P4", "finished long ago")
    g.done("T6", _ts(1))
    g.add("item.completed", "P4", {"kind": "phase"}, _ts(1))
    g.phase("P5", "Typo dependency", 50)
    g.task("T7", "P5", "needs a ghost", needs=["NOPE"])
    return g


def _body(kind: str, q: query.Query, **f) -> str:
    return registry.render_body(kind, q, Filters(**f))


def test_kinds_registered_with_shipped_templates():
    for name in ("roadmap", "bugs", "status"):
        k = registry.get(name)
        assert k.default_target == f"{name.upper()}.md"
        assert registry.shipped_template(name).text  # the package ships it


def test_roadmap_lanes_follow_state_and_dependencies():
    body = _body("roadmap", road_log().query())
    lanes = {s.split("\n", 1)[0].split(" (")[0]: s for s in body.split("\n## ")[1:]}
    assert set(lanes) == {"Now", "Next", "Later"}
    assert "### P1: First phase (1/3)" in lanes["Now"]
    assert "`T2` running thing _(running, w1)_" in lanes["Now"]
    assert "`T3` waits on running _(blocked: waits on T2)_" in lanes["Now"]
    assert "`T1`" not in body  # finished tasks are counted, not listed
    assert "### P2: Second phase (0/1)" in lanes["Next"]
    assert "`T4` ready thing\n" in lanes["Next"]
    # P3 inherits P2's dependency; P5 names an id that does not exist: both wait.
    assert "### P3: Third phase (0/1)" in lanes["Later"]
    assert "`T5` inherits the phase dependency _(blocked: waits on P2)_" in lanes["Later"]
    assert "waits on NOPE" in lanes["Later"]
    assert "1 phases complete (not listed)" in body  # P4
    assert "P4" not in body.split("(not listed)", 1)[1]


def test_roadmap_phase_dependency_met_moves_phase_to_next():
    g = road_log()
    g.done("T4", _ts(3))
    # P2 closed: P3's inherited dependency is met. Its tasks alone do not do it -- the
    # scheduler waits for the phase itself (`schedule.dep_status`, B58010c24b3).
    g.add("item.completed", "P2", {"kind": "phase"}, _ts(3))
    body = _body("roadmap", g.query())
    nxt = body.split("## Next", 1)[1].split("## Later", 1)[0]
    assert "### P3" in nxt and "`T5` inherits the phase dependency\n" in nxt


def test_roadmap_filters_and_limit():
    q = road_log().query()
    one = _body("roadmap", q, phase="P2")
    assert "P2" in one and "P1" not in one and "P3" not in one
    cut = _body("roadmap", q, limit=1)
    assert "more phases not listed" in cut
    assert "### P1" in cut and "### P5" not in cut.split("## Later", 1)[0]
    with pytest.raises(ExportError) as e:
        _body("roadmap", q, status="open")
    assert e.value.code == EXIT_REFUSED


def test_roadmap_is_deterministic_across_event_order_and_has_no_clock(monkeypatch):
    g = road_log()
    docs = [registry.render_document("roadmap", g.query(rev)) for rev in (False, False, True)]
    assert docs[0] == docs[1] == docs[2]
    keys = [e.sort_key() for e in g.events]
    assert len(set(keys)) == len(keys)  # a total order: no ties for a fold order to break
    q, r = g.query(), g.query(reverse=True)
    assert list(q.state.items) == list(reversed(list(r.state.items)))  # the order really differs
    assert not frame.hand_edited(docs[0])

    def boom():  # a body that read the clock would differ run to run
        raise AssertionError("the clock was read")

    monkeypatch.setattr(time, "time", boom)
    assert registry.render_document("roadmap", g.query()) == docs[0]


def test_roadmap_phase_filter_scopes_the_count_and_validates():
    q = road_log().query()
    assert _body("roadmap", q, phase="P2").startswith("# Roadmap\n\n1 open tasks.")
    with pytest.raises(ExportError) as e:
        _body("roadmap", q, phase="NOPE")
    assert e.value.code == EXIT_REFUSED
    with pytest.raises(ExportError):
        _body("roadmap", q, phase="T1")  # a task is not a phase


def test_roadmap_empty_phases_and_dependency_in_review():
    g = road_log()
    g.phase("P6", "No tasks yet", 60)
    g.phase("P7", "Empty but waiting", 70, needs=["P1"])
    g.task("T8", "P2", "stacks on a review", needs=["T9"])
    g.task("T9", "P2", "in review")
    g.add("pr.opened", "T9", {"branch": "feat/x"}, _ts(3))
    q = g.query()
    assert q.state.items["T9"].state == "review"
    q.state.items["T9"].branch = "feat/x"
    body = _body("roadmap", q)
    nxt = body.split("## Next", 1)[1].split("## Later", 1)[0]
    later = body.split("## Later", 1)[1]
    assert "### P6: No tasks yet (0/0)" in nxt
    assert "### P7: Empty but waiting (0/0)" in later and "Waits on P1." in later
    t8 = next(ln for ln in body.splitlines() if "`T8`" in ln)
    assert "blocked" not in t8  # a dependency in review with a branch is stackable
    g2 = road_log()
    g2.add("phase.added", "P6", {"title": "Closed empty", "priority": 60}, _ts(1))
    g2.add("item.completed", "P6", {"kind": "phase"}, _ts(2))
    assert "2 phases complete (not listed)" in _body("roadmap", g2.query())


def test_roadmap_scale_uses_the_index():
    g = Log()
    for p in range(500):
        g.phase(f"P{p:04}", f"phase {p}", p)
        for t in range(10):
            g.task(f"P{p:04}.T{t}", f"P{p:04}", f"task {t}")
    q = g.query()
    start = time.perf_counter()
    body = _body("roadmap", q)
    assert time.perf_counter() - start < 2.0
    assert "P0499" in body


# -- bugs ---------------------------------------------------------------------------------


def bug_log(n_fixed: int = 60) -> Log:
    g = Log()
    g.phase("P1", "Phase", 10)
    g.task("T1", "P1", "a task")
    g.task("T2", "P1", "other task")
    g.bug("Ba1b2c3d4e5", "The first is old. It has a second sentence that is dropped.", 0, "T1")
    g.bug("Bf6a7b8c9d0", "Newer open bug\nwith a second line", 5)
    for i in range(n_fixed):
        bid = f"B{i:011x}"
        g.bug(bid, f"Fixed bug number {i}. Details follow.", 1, "T2" if i % 2 else "")
        g.fixed(bid, 2 + i / 10, f"tests/test_a.py::test_{i}")
    g.bug("Bdeadbeef001", "Not a bug at all", 3)
    g.invalid("Bdeadbeef001", 4, "works as designed")
    g.add("item.completed", "T1", {}, _ts(30))  # the newest event: the age reference
    return g


def test_hash_id_bugs_render_without_a_title_and_clip_to_first_sentence():
    body = _body("bugs", bug_log().query())
    assert "- `Ba1b2c3d4e5` The first is old. _(found 2026-09-01, 30d old, in T1)_" in body
    assert "- `Bf6a7b8c9d0` Newer open bug _(found 2026-09-06, 25d old)_" in body
    assert "****" not in body and "**" not in body  # no empty bold title
    assert "second sentence" not in body
    # Sections and counts; open oldest first, fixed newest first, invalid with its reason.
    assert body.index("## Open (2)") < body.index("## Fixed (60)") < body.index("## Invalid (1)")
    assert body.index("Ba1b2c3d4e5") < body.index("Bf6a7b8c9d0")
    fixed = body.split("## Fixed")[1].split("## Invalid")[0]
    assert fixed.index("test_59") < fixed.index("test_0`")
    assert "_(fixed 2026-09-09; test `tests/test_a.py::test_59`)_" in fixed
    assert "- `Bdeadbeef001` Not a bug at all _(invalid 2026-09-05: works as designed)_" in body


def test_title_severity_and_scope_render_when_the_record_has_them():
    q = bug_log(0).query()
    b = q.state.bugs["Ba1b2c3d4e5"]
    b.title, b.severity, b.scope = "Stale entry", "high", "ddflow"
    body = _body("bugs", q)
    assert "- `Ba1b2c3d4e5` **Stale entry**: The first is old. [high] [ddflow] _(found" in body


def test_default_project_scope_is_not_tagged_but_other_scopes_are():
    q = bug_log(0).query()
    b = q.state.bugs["Ba1b2c3d4e5"]
    b.scope = "project"
    assert "[project]" not in _body("bugs", q)
    b.scope = None
    assert "[None]" not in _body("bugs", q)
    b.scope = "ddflow"
    assert "[ddflow]" in _body("bugs", q)


def test_filters_cut_the_unfiltered_document_to_the_slice():
    q = bug_log().query()
    full = registry.render_document("bugs", q, max_bytes=0)
    only_open = registry.render_document("bugs", q, Filters(status="open"), max_bytes=0)
    assert "## Fixed" not in only_open and "## Invalid" not in only_open
    assert len(only_open) * 4 < len(full)
    top = _body("bugs", q, status="fixed", limit=3)
    assert top.count("\n- `") == 3 and "57 more not listed (--limit)" in top
    assert "## Open" not in top
    recent = _body("bugs", q, status="fixed", since="2026-09-08")
    assert recent.count("\n- `") < 60 and "test_59" in recent and "test_0`" not in recent
    # since cuts every section by its own date: nothing was found after day 5.
    late = _body("bugs", q, since="2026-09-07")
    assert "## Open (0)" in late and "## Invalid (0)" in late
    # a timestamp works too, and a malformed one is refused, not ignored
    assert "## Open (1)" in _body("bugs", q, status="open", since=_ts(3))
    with pytest.raises(ExportError) as e:
        _body("bugs", q, since="last week")
    assert e.value.code == EXIT_REFUSED


def test_bugs_item_scope_accepts_a_task_id():
    q = bug_log().query()
    body = _body("bugs", q, phase="T1", status="open")  # what a surface's --item maps to
    assert "Ba1b2c3d4e5" in body and "Bf6a7b8c9d0" not in body
    t2 = _body("bugs", q, phase="T2", status="fixed")
    assert "## Fixed (30)" in t2


def test_bugs_filter_by_phase_and_refuse_bad_values():
    q = bug_log().query()
    body = _body("bugs", q, phase="P1", status="open")
    assert "Ba1b2c3d4e5" in body and "Bf6a7b8c9d0" not in body  # only T1's
    with pytest.raises(ExportError) as e:
        _body("bugs", q, status="wip")
    assert e.value.code == EXIT_REFUSED
    with pytest.raises(ExportError) as e:
        _body("bugs", q, phase="NOPE")
    assert e.value.code == EXIT_REFUSED


def test_bugs_deterministic_and_markdown_safe():
    g = bug_log(5)
    g.bug("Bc0ffee0001", "Pipe | star *x* and `tick` <tag>", 6)
    a = registry.render_document("bugs", g.query())
    assert a == registry.render_document("bugs", g.query(reverse=True))
    assert "\\|" in a and "\\*" in a and "\\`tick\\`" in a and "\\<tag>" in a


def test_a_bug_with_an_unreadable_date_does_not_break_the_document():
    g = Log()
    g.add("bug.found", "Bnodate0001", {"summary": "No usable time"}, "garbage")
    body = _body("bugs", g.query())
    assert "`Bnodate0001` No usable time" in body and "d old" not in body


# -- status -------------------------------------------------------------------------------


def test_status_counts_in_flight_and_agent_hours(monkeypatch):
    g = road_log()
    g.bug("Bb1", "open one", 1)
    g.bug("Bb2", "closed one", 1)
    g.fixed("Bb2", 2)
    g.add("decision.recorded", "D1", {"title": "A decision", "status": "accepted"}, _ts(2))
    g.add("lease.released", "T2", {}, _ts(2, 3))  # held 3h, then re-claimed and held to the end
    g.run("T2", _ts(2, 4))
    g.add("note", "x", {}, _ts(2, 6))  # newest event: the open attempt ends here
    q = g.query()
    monkeypatch.setattr(time, "time", lambda: 1e12)  # the clock must not matter
    body = _body("status", q)
    assert "- Tasks: 2/7 done" in body and "across 5 phases (1 complete)" in body
    assert "- By state: 2 done, 1 running, 4 open." in body
    assert "- Agent-hours: 5.0." in body  # 3h closed + 2h open to the newest event
    assert "- Open bugs: 1 of 2." in body
    assert "As of 2026-09-03." in body
    assert "- `T2` running thing (running, w1)" in body
    assert registry.render_document("status", q) == registry.render_document("status", q)


def test_status_takes_no_filters():
    with pytest.raises(ExportError) as e:
        _body("status", road_log().query(), limit=3)
    assert e.value.code == EXIT_REFUSED


def test_empty_log_renders_empty_sections_not_errors():
    q = Log().query()
    assert "None." in _body("roadmap", q)
    assert "## Open (0)" in _body("bugs", q)
    assert "0/0 done" in _body("status", q)
