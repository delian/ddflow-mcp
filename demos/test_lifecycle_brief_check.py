"""Regression for B-lifecycle-brief-check-vacuous.

``lifecycle_support.work()`` used to assert ``"D1" in brief or True``, which is
always true, so a ``ddflow_brief`` that failed to surface the D1 decision for the
money task could never fail the check. A client whose brief omits D1 must now
raise ``Fail`` for a D1-governed file.
"""

from __future__ import annotations

from pathlib import Path

import lifecycle_support
import pytest


class _MoneyBriefClient:
    """Answers only the two calls ``work()`` makes before the brief check."""

    def __init__(self, brief: str, worktree: Path) -> None:
        self._brief = brief
        self._worktree = worktree

    def jtool(self, name: str, **args):
        assert name == "ddflow_claim", name
        return {"worktree": str(self._worktree)}

    def tool(self, name: str, **args):
        assert name == "ddflow_brief", name
        return (self._brief, 0)


class _FailingCheck(AssertionError):
    pass


class _Scenario:
    def check(self, label: str, cond: bool, detail: str = "") -> None:
        if not cond:
            raise _FailingCheck(f"{label}\n{detail}")


def test_brief_omitting_d1_fails_for_the_money_task(tmp_path):
    wt_root = tmp_path / "worktrees"
    wt_root.mkdir()
    wt = wt_root / "P1.T1"
    wt.mkdir()
    client = _MoneyBriefClient("this brief forgot the decision", wt)
    sc = _Scenario()
    # money.py is a file D1 governs, so a brief that omits D1 is a real failure —
    # not a vacuous pass the way `"D1" in brief or True` always was.
    with pytest.raises(_FailingCheck):
        lifecycle_support.work(
            sc, client, tmp_path, wt_root, "P1.T1", {"ledger/core/money.py": "x = 1"}
        )


def test_brief_mentioning_d10_does_not_stand_in_for_d1(tmp_path):
    wt_root = tmp_path / "worktrees"
    wt_root.mkdir()
    wt = wt_root / "P1.T1"
    wt.mkdir()
    # `id="D10"` contains "D1" as a bare substring but is not decision D1: it must
    # not satisfy the money task's check.
    client = _MoneyBriefClient('<ddflow-record kind="decision" id="D10">', wt)
    sc = _Scenario()
    with pytest.raises(_FailingCheck):
        lifecycle_support.work(
            sc, client, tmp_path, wt_root, "P1.T1", {"ledger/core/money.py": "x = 1"}
        )


def test_brief_mentioning_related_id_d1_does_not_stand_in_for_d1(tmp_path):
    wt_root = tmp_path / "worktrees"
    wt_root.mkdir()
    wt = wt_root / "P1.T1"
    wt.mkdir()
    # `related_id="D1"` mentions D1 in a different attribute but is not the D1
    # record: only the standalone `id="D1"` attribute counts.
    client = _MoneyBriefClient('<ddflow-record kind="decision" id="D2" related_id="D1">', wt)
    sc = _Scenario()
    with pytest.raises(_FailingCheck):
        lifecycle_support.work(
            sc, client, tmp_path, wt_root, "P1.T1", {"ledger/core/money.py": "x = 1"}
        )


def test_brief_broadcasting_d1_to_an_ungoverned_file_fails(tmp_path):
    wt_root = tmp_path / "worktrees"
    wt_root.mkdir()
    wt = wt_root / "P1.T2"
    wt.mkdir()
    # account.py is NOT a file D1 governs; a brief that surfaces D1 for it is the
    # "broadcast, not scoped" failure the scenario's other assertions exist to catch.
    client = _MoneyBriefClient('<ddflow-record kind="decision" id="D1">', wt)
    sc = _Scenario()
    with pytest.raises(_FailingCheck):
        lifecycle_support.work(
            sc, client, tmp_path, wt_root, "P1.T2", {"ledger/core/account.py": "x = 1"}
        )
