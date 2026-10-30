"""DRV-facts: version range, expiry, unknown-degrades, drift doctor on the one fact record."""

from __future__ import annotations

from datetime import date

from ddflow.services import factdrift, facts

TODAY = date(2026, 10, 30)


def fact(**kw):
    base = {"v": "x", "s": "verified", "src": "probe", "d": "2026-10-01"}
    base.update(kw)
    return base


def test_new_keys_are_valid_and_checked():
    ok = fact(versions=">=1.2,<2", expires="2026-12-01", probe="tool --version", by="probe")
    assert facts.problems("f", ok) == []
    bad = fact(versions=3, expires="soon", probe=1, by=2)
    msgs = " ".join(facts.problems("f", bad))
    for key in ("versions", "expires", "probe", "by"):
        assert key in msgs


def test_unparseable_range_is_a_problem():
    assert any("versions" in p for p in facts.problems("f", fact(versions=">=x")))


def test_expired():
    a = factdrift.assess(fact(expires="2026-10-29"), today=TODAY)
    assert a.state == "expired" and "2026-10-29" in a.reason
    assert factdrift.assess(fact(expires="2026-10-30"), today=TODAY).state == "ok"
    assert factdrift.assess(fact(expires="2026-10-29"), today=TODAY, grace_days=2).state == "ok"


def test_out_of_range():
    f = fact(versions=">=1.2,<2")
    assert factdrift.assess(f, installed="1.5.3", today=TODAY).state == "ok"
    a = factdrift.assess(f, installed="2.0", today=TODAY)
    assert a.state == "out_of_range" and "2.0" in a.reason
    assert factdrift.assess(f, installed="1.1.9", today=TODAY).state == "out_of_range"
    assert factdrift.assess(fact(versions="1.4"), installed="1.4.0", today=TODAY).state == "ok"


def test_unknown_installed_version_is_unknown_not_ok():
    a = factdrift.assess(fact(versions=">=1"), installed=None, today=TODAY)
    assert a.state == "unknown" and "version" in a.reason


def test_no_constraints_is_ok():
    assert factdrift.assess(fact(), today=TODAY).state == "ok"


def test_drifted_fact_demoted_to_unverified():
    f = fact(versions="<2")
    out = factdrift.effective(f, installed="3.0", today=TODAY)
    assert out["s"] == "unverified" and f["s"] == "verified"
    assert "out_of_range" in out["note"]
    same = factdrift.effective(f, installed="1.0", today=TODAY)
    assert same == f


def test_none_status_is_not_demoted_below_itself():
    f = fact(s="none", expires="2020-01-01")
    assert factdrift.effective(f, today=TODAY)["s"] == "unverified"


def test_observation_outranks_document():
    doc = fact(s="documented", v="doc")
    obs = fact(s="verified", v="obs")
    assert facts.rank(obs) > facts.rank(doc) > facts.rank(fact(s="unverified"))
    assert facts.best([doc, obs])["v"] == "obs"


def test_best_ignores_drifted_observation_in_favor_of_document():
    doc = fact(s="documented", v="doc")
    stale = fact(s="verified", v="obs", expires="2020-01-01")
    got = factdrift.pick([stale, doc], today=TODAY)
    assert got.value == "doc" and got.fact is doc


def test_unknown_yields_fallback_with_reason():
    got = factdrift.pick([], fallback=8000, today=TODAY, what="ctx")
    assert got.value == 8000 and got.fact is None and "ctx" in got.reason
    stale = fact(s="verified", expires="2020-01-01")
    got = factdrift.pick([stale], fallback=1, today=TODAY)
    assert got.value == 1 and "expired" in got.reason
    unv = fact(s="unverified", v="lead")
    got = factdrift.pick([unv], fallback=1, today=TODAY)
    assert got.value == 1


def test_none_fact_is_a_known_absence():
    got = factdrift.pick([fact(s="none", v=None)], fallback=5, today=TODAY)
    assert got.fact is not None and got.value is None


def test_doctor_flags_expired_and_out_of_range():
    table = {
        "a": fact(expires="2020-01-01"),
        "b": fact(versions="<2", ver="1.0"),
        "c": fact(),
    }
    found = factdrift.doctor(table, installed={"b": "3.0"}, today=TODAY)
    by = {x.key: x for x in found}
    assert set(by) == {"a", "b"}
    assert by["a"].state == "expired" and by["b"].state == "out_of_range"
    lines = factdrift.report(found)
    assert any("a" in ln and "expired" in ln for ln in lines)
    assert factdrift.report([]) == []
