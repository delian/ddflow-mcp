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


def test_a_cut_test_list_in_cis_record_runs_the_selection_not_the_whole_suite(monkeypatch):
    sel = NS(full="", tests=[Selected("tests/a.py", "")], cut=[], cut_at=0)
    ran = {**RAN, "selected": 5, "tests": ["tests/other.py"]}  # five reached, one listed
    monkeypatch.setattr(TSC, "run_command", lambda cmd, paths, root: "pytest " + " ".join(paths))
    cfg = NS(gates=NS(unit_tests_max_files=10))
    monkeypatch.setattr(TSC, "select", lambda *a, **k: sel)
    monkeypatch.setattr(TSC, "limits", lambda cfg: {})
    sc = TSC._after_ci(cfg, NS(bugs={}), NS(fixes=[]), "pytest -q", Path("."), "main", 3, ran)
    assert sc.scope == "selected" and sc.command == "pytest tests/a.py", sc


def test_a_command_that_does_not_run_pytest_keeps_its_own_command_for_the_rest(monkeypatch):
    sel = NS(full="", tests=[Selected("tests/b.py", "")], cut=[], cut_at=0)
    monkeypatch.setattr(TSC, "select", lambda *a, **k: sel)
    monkeypatch.setattr(TSC, "limits", lambda cfg: {})
    monkeypatch.setattr(TSC, "run_command", lambda *a: "")  # the project does not run pytest
    sc = TSC._after_ci(NS(), NS(bugs={}), NS(fixes=[]), "make unit", Path("."), "main", 3, RAN)
    assert sc.scope == "full" and "does not run pytest" in sc.why, sc
