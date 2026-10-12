"""doctor must not print one orphan_worktree note per adopted harness worktree.

Those trees are the harness's own and doctor itself says to leave them alone, so a
clean adopted one is folded into a single summary note. A real orphan (not adopted,
or holding work) still gets its own line.
"""

from __future__ import annotations

from conftest import run_cli

from ddflow.api import reporting as R
from ddflow.services import leases as L


def _rec(item: str, *, adopted: bool, salvageable: bool | None, tree: str = "") -> L.Recovery:
    return L.Recovery(
        item,
        "(none)",
        "orphan_worktree",
        adopted=adopted,
        worktree=f"/x/{tree or item}",
        salvageable=salvageable,
        advice=f"advice for {item}",
    )


def test_clean_adopted_orphans_collapse_to_one_note(repo, monkeypatch):
    run_cli(repo, "init")
    recs = [
        _rec("A1", adopted=True, salvageable=False),
        _rec("A2", adopted=True, salvageable=False, tree="A1"),
        _rec("A3", adopted=True, salvageable=False),
        _rec("R1", adopted=False, salvageable=False),
    ]
    monkeypatch.setattr(L, "scan", lambda *a, **k: recs)
    d = R.doctor(repo).data
    orphan_notes = [n for n in d["notes"] if n.startswith("orphan_worktree")]
    assert len(orphan_notes) == 2, orphan_notes
    assert any(n.startswith("orphan_worktree: R1") for n in orphan_notes), orphan_notes
    summary = next(n for n in orphan_notes if "R1" not in n)
    assert "3 item(s) on 2 adopted" in summary, summary
    assert not any(f"orphan_worktree: A{i}" in n for i in (1, 2, 3) for n in d["notes"])


def test_adopted_orphan_holding_work_stays_a_problem(repo, monkeypatch):
    run_cli(repo, "init")
    monkeypatch.setattr(L, "scan", lambda *a, **k: [_rec("A1", adopted=True, salvageable=True)])
    d = R.doctor(repo).data
    assert any("orphan_worktree: A1" in p for p in d["problems"]), d["problems"]
