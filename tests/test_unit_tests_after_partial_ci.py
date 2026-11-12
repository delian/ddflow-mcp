"""Bug B-unit-tests-refuses-on-partial-ci-red-main: a full ci that ran on this very tree and
ended partial (a red main) does not stop a bug fix's unit_tests from running its selection."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from ddflow.services import testscope as TSC

TREE = "0feb96bab022+clean"


def _it(outcome: str, tree: str):
    ci = NS(outcome=outcome, evidence={"tree_sha": tree})
    return NS(
        fixes=["B1"], kind="task", promote_to="", base="main", id="T", gates={"ci": ci}, bugs={}
    )


def _scope(monkeypatch, outcome: str, tree: str = TREE):
    cfg = NS(gates=NS(unit_tests_scope="selected", unit_tests_small_lines=0), worktree=NS())
    monkeypatch.setattr(TSC.G, "tree_identity", lambda t: TREE)
    monkeypatch.setattr(TSC, "_pipeline", lambda *a: ["ci", "unit_tests"])
    monkeypatch.setattr(TSC, "_is_trivial", lambda *a: False)
    monkeypatch.setattr(TSC, "changed_lines", lambda *a: 10)
    monkeypatch.setattr(TSC, "_selected", lambda *a, **k: NS(scope="selected", needs_ci=False))
    return TSC.unit_tests_scope(cfg, NS(bugs={}), _it(outcome, tree), "pytest", Path("."))


def test_a_partial_ci_measured_on_this_tree_does_not_refuse_unit_tests(monkeypatch):
    sc = _scope(monkeypatch, "partial")
    assert not sc.needs_ci and sc.scope == "selected", sc


@pytest.mark.parametrize("outcome", ["unavailable", "skipped", ""])
def test_an_unmeasured_ci_still_refuses(monkeypatch, outcome):
    assert _scope(monkeypatch, outcome).needs_ci


def test_a_partial_ci_of_another_tree_still_refuses(monkeypatch):
    assert _scope(monkeypatch, "partial", "aaaaaaaaaaaa+clean").needs_ci
