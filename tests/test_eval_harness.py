"""The evaluation harness's scoring (SE-eval-harness.1) and its fixture kind."""

from __future__ import annotations

import pytest

import ddflow.api.records as R
import ddflow.core.evalharness as E

K = {"max_miss_rate": 0.0, "min_fixtures": 1}
FIX = {"id": "F1", "workflow": "wf", "planted": ["sql=injection|concat", "race=lock"]}


def test_catches_and_misses_by_word():
    s = E.score(FIX, ["SQL Injection in query", "style nit"])
    assert s.caught == ("sql",) and s.missed == ("race",) and s.extra == ("style nit",)
    assert s.planted == 2


def test_clean_fixture_counts_every_finding_extra():
    s = E.score({"id": "C", "workflow": "wf", "planted": []}, ["noise"])
    assert s.planted == 0 and s.extra == ("noise",)
    assert E.miss_rate([s]) is None


@pytest.mark.parametrize("bad", ["nogoal", "=x", "id=", "id=|"])
def test_bad_fault_entry_is_refused(bad):
    with pytest.raises(E.FaultSpecError):
        E.parse_fault(bad)


def test_duplicate_fault_id_is_refused():
    with pytest.raises(E.FaultSpecError):
        E.parse_faults(["a=x", "a=y"])


def test_report_and_enable_gate():
    rows = E.report(
        [E.score(FIX, ["injection", "lock"]), E.score({**FIX, "id": "F2", "workflow": "bad"}, [])],
        **K,
    )
    assert rows["wf"]["miss_rate"] == 0 and rows["bad"]["miss_rate"] == 1
    assert rows["wf"]["enabled"] and not rows["bad"]["enabled"] and "2 of 2" in rows["bad"]["why"]
    assert E.enable_by_default(rows["wf"], **K)[0]
    ok, why = E.enable_by_default(rows["bad"], **K)
    assert not ok and "2 of 2" in why
    assert E.enable_by_default(rows["bad"], max_miss_rate=1.0, min_fixtures=1)[0]
    assert not E.enable_by_default(None, **K)[0]
    assert not E.enable_by_default(rows["wf"], max_miss_rate=0.0, min_fixtures=2)[0]


def test_fixture_kind_is_a_record_kind():
    kind = R.KINDS["evalfixture"]
    assert kind.field("planted").type == "array" and kind.field("workflow").required
