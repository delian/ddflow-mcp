"""The enforcement plumbing of the guidance engine (B-uni-guidance-enforce).

Checks, waivers and review dates attach to ANY guidance record: a rule and a decision go
through the same functions, which is the point (D-unify, Operator 2026-10-06), so every test
here that can run both kinds does.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from ddflow.core.model import State
from ddflow.services import approval as AP
from ddflow.services.guidance import checks as CK
from ddflow.services.guidance import review as RV
from ddflow.services.guidance import waivers as WV
from ddflow.services.guidance.record import GuidanceRecord, Scope

TODAY = date(2026, 10, 8)


def _rec(rid="g-1", kind="rule", checks=None, **kw) -> GuidanceRecord:
    return GuidanceRecord(id=rid, kind=kind, title=rid, checks=checks or [], **kw)


@pytest.fixture
def kinds():
    """Isolated check kinds: the registry is process-wide."""
    saved = dict(CK._KINDS)
    CK._KINDS.clear()
    yield
    CK._KINDS.clear()
    CK._KINDS.update(saved)


def _ctx(*paths):
    return CK.CheckContext(Path("."), tuple(paths))


def _flag_all(check, ctx):
    return [CK.Finding(p, "bad", 3) for p in ctx.paths]


# -- checks ----------------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["rule", "decision"])
def test_a_check_passes_fails_and_scopes_alike_for_a_rule_and_a_decision(kind, kinds):
    CK.register("flag", _flag_all)
    rec = _rec(kind=kind, checks=[{"kind": "flag"}], scope=Scope(globs=("src/**",)))
    [res] = CK.run_record(rec, _ctx("src/a.py", "docs/b.md"))
    assert res.status == CK.FAIL and [f.path for f in res.findings] == ["src/a.py"]
    assert res.check_id == "g-1#1" and res.findings[0].where() == "src/a.py:3"
    [res] = CK.run_record(rec, _ctx("docs/b.md"))
    assert res.status == CK.PASS and not res.findings


def test_a_check_that_cannot_be_evaluated_is_unavailable_never_a_pass(kinds):
    def boom(check, ctx):
        raise RuntimeError("tool crashed")

    def missing(check, ctx):
        raise CK.Unavailable("semgrep is not installed")

    CK.register("boom", boom)
    CK.register("missing", missing)
    CK.register("junk", lambda c, x: "not a list")
    rec = _rec(
        checks=[{"kind": "nope"}, {"kind": "boom"}, {"kind": "missing"}, {"kind": "junk"}, {}, 3]
    )
    got = CK.run_record(rec, _ctx("a.py"))
    assert [r.status for r in got] == [CK.UNAVAILABLE] * 6
    assert "no check kind 'nope'" in got[0].reason
    assert "RuntimeError: tool crashed" in got[1].reason
    assert got[2].reason == "semgrep is not installed"
    assert "list of findings" in got[3].reason and "needs a kind" in got[4].reason
    assert CK.gate_verdict([rec], got).outcome == "unavailable"


def test_only_live_guidance_is_held_to_its_checks(kinds):
    CK.register("flag", _flag_all)
    assert CK.run_record(_rec(checks=[{"kind": "flag"}], status="retired"), _ctx("a")) == []
    assert CK.run_record(_rec(checks=[{"kind": "flag"}], status="proposed"), _ctx("a")) == []


def test_a_kind_registers_once_and_has_a_plain_name(kinds):
    CK.register("deps", _flag_all)
    with pytest.raises(ValueError, match="already registered"):
        CK.register("deps", _flag_all)
    with pytest.raises(ValueError, match="letters"):
        CK.register("bad name", _flag_all)
    assert CK.kinds() == ["deps"]


def test_the_gate_verdict_follows_enforcement_and_never_hides_what_it_found(kinds):
    CK.register("flag", _flag_all)
    CK.register("ok", lambda c, x: [])
    blocking = _rec("b", enforcement="block", checks=[{"kind": "flag"}])
    warning = _rec("w", enforcement="warn", checks=[{"kind": "flag"}])
    advisory = _rec("a", enforcement="advisory", checks=[{"kind": "flag"}])
    fine = _rec("f", enforcement="block", checks=[{"kind": "ok"}])
    recs = [blocking, warning, advisory, fine]
    results = [r for rec in recs for r in CK.run_record(rec, _ctx("x.py"))]
    v = CK.gate_verdict(recs, results)
    assert v.outcome == "failed"
    assert [r.record for r in v.blocking] == ["b"]
    assert [r.record for r in v.warnings] == ["w"] and [r.record for r in v.notes] == ["a"]
    assert v.summary() == "1 blocking, 1 warning, 1 note"
    # without the blocking record the gate passes, still carrying the warning
    rest = [r for r in results if r.record != "b"]
    v = CK.gate_verdict(recs, rest)
    assert v.outcome == "passed" and len(v.warnings) == 1
    assert CK.gate_verdict([], []).outcome == "passed"
    # a failure beats an unavailable one
    both = [*results, CK.CheckResult("f", "f#2", "x", CK.UNAVAILABLE, reason="no tool")]
    assert CK.gate_verdict(recs, both).outcome == "failed"


def test_equal_outcomes_have_equal_digests(kinds):
    CK.register("flag", _flag_all)
    rec = _rec(checks=[{"kind": "flag"}])
    a = CK.run_record(rec, _ctx("a.py"))[0]
    b = CK.run_record(rec, _ctx("a.py"))[0]
    c = CK.run_record(rec, _ctx("b.py"))[0]
    assert a.digest == b.digest != c.digest


# -- waivers ---------------------------------------------------------------------------------


def _waiver(**kw) -> WV.Waiver:
    base = {
        "id": "w1",
        "record": "g-1",
        "reason": "legacy module, rewrite planned",
        "globs": ("src/legacy/**",),
        "expires": "2026-11-01",
        "granted": "2026-10-08",
    }
    base.update(kw)
    return WV.Waiver(**base)


def test_a_waiver_needs_a_reason_scope_and_at_most_ninety_days():
    assert WV.validate(_waiver()) == []
    assert WV.validate(_waiver(reason="  ")) == ["a waiver needs a reason"]
    assert "scoped to files" in WV.validate(_waiver(globs=()))[0]
    assert WV.validate(_waiver(expires="2027-01-06")) == []  # exactly 90 days
    assert "at most 90 days" in WV.validate(_waiver(expires="2027-01-07"))[0]
    assert "before it was granted" in WV.validate(_waiver(expires="2026-10-01"))[0]
    assert "not a date" in WV.validate(_waiver(expires="soon"))[0]
    assert "not a date" in WV.validate(_waiver(granted="never"))[0]


def test_a_waiver_is_active_only_while_approved_and_unexpired():
    w = _waiver()
    yes, no = (lambda _w: True), (lambda _w: False)
    assert WV.status(w, TODAY, yes) == WV.ACTIVE
    assert WV.status(w, TODAY, no) == WV.UNAPPROVED
    assert WV.status(w, date(2026, 11, 1), yes) == WV.ACTIVE  # its last day
    assert WV.status(w, date(2026, 11, 2), yes) == WV.EXPIRED
    assert WV.status(_waiver(expires="x"), TODAY, yes) == WV.EXPIRED


def test_approval_is_of_the_exact_waiver_a_person_saw():
    st = State()
    w = _waiver()
    assert WV.approved_in(st)(w) is False
    st.approvals[w.subject] = [
        {"digest": w.digest, "human": True, "token_hash": "", "used_at": "", "user": "me"}
    ]
    assert WV.approved_in(st)(w) is True
    widened = _waiver(globs=("src/**",))
    assert widened.digest != w.digest and WV.approved_in(st)(widened) is False
    assert AP.check(st, w.subject, w.digest).ok


@pytest.mark.parametrize("kind", ["rule", "decision"])
def test_a_waiver_covers_its_scope_and_expiry_resurfaces_the_finding(kind, kinds):
    CK.register("flag", _flag_all)
    rec = _rec(kind=kind, enforcement="block", checks=[{"kind": "flag"}])
    results = CK.run_record(rec, _ctx("src/legacy/a.py", "src/new/b.py"))
    approved = lambda _w: True  # noqa: E731
    got = WV.apply(results, [_waiver()], today=TODAY, approved=approved)
    [r] = got
    assert r.status == CK.FAIL  # src/new/b.py is not covered
    assert [f.path for f in r.findings] == ["src/new/b.py"]
    assert [f.path for f in r.waived] == ["src/legacy/a.py"] and r.waivers == ("w1",)
    only = CK.run_record(rec, _ctx("src/legacy/a.py"))
    [r] = WV.apply(only, [_waiver()], today=TODAY, approved=approved)
    assert r.status == CK.WAIVED and not r.findings
    v = CK.gate_verdict([rec], [r])
    assert v.outcome == "passed" and v.waived == [r]
    # the day after it expires the finding is live again, and the gate fails
    [r] = WV.apply(only, [_waiver()], today=date(2026, 11, 2), approved=approved)
    assert r.status == CK.FAIL and CK.gate_verdict([rec], [r]).outcome == "failed"
    assert [w.id for w in WV.resurfaced([_waiver()], date(2026, 11, 2))] == ["w1"]
    assert WV.resurfaced([_waiver()], TODAY) == []


def test_an_unapproved_or_invalid_waiver_or_another_check_covers_nothing(kinds):
    CK.register("flag", _flag_all)
    rec = _rec(checks=[{"kind": "flag", "id": "c1"}, {"kind": "flag", "id": "c2"}])
    results = CK.run_record(rec, _ctx("src/legacy/a.py"))
    yes = lambda _w: True  # noqa: E731
    for w, ok in (
        (_waiver(), lambda _w: False),  # nobody approved it
        (_waiver(reason=""), yes),  # invalid
        (_waiver(record="other"), yes),  # another record
    ):
        assert all(r.status == CK.FAIL for r in WV.apply(results, [w], today=TODAY, approved=ok))
    got = WV.apply(results, [_waiver(check="c1")], today=TODAY, approved=yes)
    assert [r.status for r in got] == [CK.WAIVED, CK.FAIL]


def test_expiring_soon_is_a_heads_up_before_the_lapse():
    ws = [_waiver(id="a", expires="2026-10-12"), _waiver(id="b", expires="2026-12-01")]
    assert [w.id for w in WV.expiring(ws, TODAY)] == ["a"]
    assert WV.expiring(ws, TODAY, within_days=100) == sorted(ws, key=lambda w: w.expires)


# -- review ----------------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["rule", "decision"])
def test_review_dates_come_due_oldest_first_for_live_guidance_only(kind):
    recs = [
        _rec("old", kind=kind, review_by="2026-09-01"),
        _rec("today", kind=kind, review_by="2026-10-08"),
        _rec("later", kind=kind, review_by="2026-10-09"),
        _rec("none", kind=kind),
        _rec("retired", kind=kind, review_by="2026-01-01", status="retired"),
        _rec("garbled", kind=kind, review_by="soon"),
    ]
    got = RV.review_due(recs, TODAY)
    assert [(d.record.id, d.overdue_days) for d in got] == [
        ("old", 37),
        ("garbled", 0),
        ("today", 0),
    ]
    assert "not a date" in got[1].reason


def test_a_revisit_trigger_fires_and_a_broken_one_is_reported_not_swallowed():
    saved = dict(RV._TRIGGERS)
    RV._TRIGGERS.clear()
    try:
        RV.register_trigger("major", lambda rec, world: world if rec.id == "g-1" else "")
        RV.register_trigger("crash", lambda rec, world: 1 / 0)
        with pytest.raises(ValueError, match="already registered"):
            RV.register_trigger("major", lambda r, w: "")
        got = RV.revisit_due([_rec("g-1"), _rec("g-2", status="retired")], "library X v3 shipped")
        assert [(d.trigger, d.record.id) for d in got] == [("crash", "g-1"), ("major", "g-1")]
        assert got[1].reason == "library X v3 shipped"
        assert "could not run (ZeroDivisionError" in got[0].reason
        both = RV.due([_rec("g-1", review_by="2026-01-01")], TODAY, "v3")
        assert [d.trigger for d in both] == ["", "crash", "major"]
    finally:
        RV._TRIGGERS.clear()
        RV._TRIGGERS.update(saved)


def test_a_recorded_review_moves_the_date_or_clears_it():
    assert RV.reviewed(RV.REAFFIRMED, TODAY, interval_days=30) == "2026-11-07"
    assert RV.reviewed(RV.REAFFIRMED, TODAY) == "2027-04-06"
    assert RV.reviewed(RV.CHANGED, TODAY) == ""
    with pytest.raises(ValueError):
        RV.reviewed("ignored", TODAY)
    with pytest.raises(ValueError):
        RV.reviewed(RV.REAFFIRMED, TODAY, interval_days=0)


def test_reminders_are_budgeted():
    recs = [_rec(f"g-{n}", review_by="2026-01-01") for n in range(8)]
    lines = RV.reminders(RV.review_due(recs, TODAY), limit=3)
    assert len(lines) == 4 and lines[-1] == "... and 5 more due for review"
    assert lines[0].startswith("rule g-0: review_by 2026-01-01 has passed (280 days)")
    assert RV.reminders([]) == []
