"""Bug Bd3b5e884c0: merge refused a measured baseline-red partial as 'only a person accepts'
without saying WHY ddflow did not accept it itself (D-baseline-red-auto-accept: it does, unless a
failing test file is the item's own or reached by it); the refusal names that reason."""

from __future__ import annotations

from types import SimpleNamespace as NS

from ddflow.core.records import GateRecord
from ddflow.services import merge_force as MF

WHY = "failing test file(s) the item changed or reaches: tests/test_x.py"


def _item():
    ev = {"baseline": {"red": True, "auto": {"accepted": False, "why": WHY}}}
    rec = GateRecord("unit_tests", "partial", "t", "a", "baseline-red: x", ev)
    return NS(gates={"unit_tests": rec}, gate_outcome=lambda g: "partial")


def test_the_refusal_names_why_the_partial_was_not_accepted_automatically():
    a = MF.Assessment(
        mode="refuse",
        unmet={"unit_tests": "partial"},
        person_only={"unit_tests": "partial"},
        why=MF.why_not_accepted(_item(), ["unit_tests"]),
    )
    msg = MF.refusal("T1", a, by_agent=True)
    assert WHY in msg and "unit_tests" in msg, msg


def _assess(monkeypatch, ev):
    rec = GateRecord("unit_tests", "partial", "t", "a", "baseline-red: x", ev)
    it = NS(gates={"unit_tests": rec}, gate_outcome=lambda g: "partial")
    monkeypatch.setattr(MF.CM, "verdict", lambda *a, **k: NS(unmet_gates=["unit_tests"]))
    cfg = NS(merge=NS(require_gates="refuse"))
    return MF.assess(None, cfg, NS(items={"T1": it}), "T1")


def test_assess_carries_the_recorded_reason_into_the_refusal(monkeypatch):
    ev = {"baseline": {"red": True, "auto": {"accepted": False, "why": WHY}}}
    a = _assess(monkeypatch, ev)
    assert a.person_only and WHY in MF.refusal("T1", a, by_agent=True)


def test_a_partial_with_no_recorded_reason_still_says_why_it_is_not_automatic(monkeypatch):
    a = _assess(monkeypatch, {"baseline": {"red": True}})
    assert a.person_only
    assert MF.NO_REASON in MF.refusal("T1", a, by_agent=True)
