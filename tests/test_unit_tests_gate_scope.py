"""Bug B9ec96cb844: with the item's selected-scope ci passed on this tree, the unit_tests gate
never starts the whole suite -- ci's recorded selected run is the evidence (D-gate-economy-r3,
D-agent-test-etiquette)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from ddflow.services import testscope as TSC
from ddflow.services.testselect import Selected

RAN = {"scope": "selected", "sha": "abcdef0123456789", "selected": 1, "tests": ["tests/a.py"]}


def _after(monkeypatch, sel, ran=RAN):
    monkeypatch.setattr(TSC, "select", lambda *a, **k: sel)
    monkeypatch.setattr(TSC, "limits", lambda cfg: {})
    return TSC._after_ci(NS(), NS(bugs={}), NS(fixes=[]), "pytest -q", Path("."), "main", 300, ran)


@pytest.mark.parametrize(
    "sel",
    [
        None,  # git could not say what changed now
        NS(full="a hub module changed: ddflow/x.py", tests=[Selected("tests/a.py", "")], cut=[]),
    ],
    ids=["unknown-change", "hub-change"],
)
def test_a_selector_that_now_wants_the_whole_suite_is_covered_by_cis_selected_run(monkeypatch, sel):
    sc = _after(monkeypatch, sel)
    assert sc.scope == "covered" and sc.covered_by == "ci selected run abcdef012345", sc


def test_a_cut_test_list_in_cis_record_is_covered_by_cis_selected_run(monkeypatch):
    sel = NS(full="", tests=[Selected("tests/a.py", "")], cut=[], cut_at=0)
    ran = {**RAN, "selected": 5}  # five reached, one listed
    sc = _after(monkeypatch, sel, ran)
    assert sc.scope == "covered" and sc.covered_by.startswith("ci selected run "), sc


def test_a_command_that_does_not_run_pytest_is_covered_by_cis_selected_run(monkeypatch):
    sel = NS(full="", tests=[Selected("tests/b.py", "")], cut=[], cut_at=0)
    monkeypatch.setattr(TSC, "select", lambda *a, **k: sel)
    monkeypatch.setattr(TSC, "limits", lambda cfg: {})
    monkeypatch.setattr(TSC, "run_command", lambda *a: "")  # the project does not run pytest
    cfg = NS(gates=NS(unit_tests_max_files=10))
    sc = TSC._after_ci(cfg, NS(bugs={}), NS(fixes=[]), "make test", Path("."), "main", 3, RAN)
    assert sc.scope == "covered" and sc.covered_by.startswith("ci selected run "), sc
