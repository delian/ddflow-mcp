"""B-review-round-budget-no-fix-path: a confirmed-and-fixed finding settles a gate whose
round budget is spent, even though the fix moved the tree the round reviewed.

Before the fix, `_settle_after_cap` returned "stays failed" for a stale round whatever the
finding's verdict, so a gate with a CONFIRMED (fixed) finding had no path once its round
budget was spent: the fix moved the item's tree, the settle was blocked as stale, and a
re-review was refused past the cap. A confirmed finding is fixed -- the moved tree IS the
fix, not unreviewed work (B835d22b138 rightly guards only REFUTATIONS over a moved tree,
which this test locks in unchanged).
"""

from __future__ import annotations

import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from helpers import git_quiet as _git

import ddflow.api.review as api
from ddflow.core.model import fold
from ddflow.infra.log import EventLog

OK = 0


def _setup(repo: Path, tmp_path: Path, findings: int = 1) -> None:
    """A command reviewer reporting ``findings`` finding(s) on every round; the item's
    tree is the uncommitted ``x.py`` (worktree disabled: no separate worktree to hold)."""
    cli = tmp_path / "fake-reviewer"
    body = "".join(f"FINDING HIGH x.py:{n}\\nproblem {n}\\n\\n" for n in range(1, findings + 1))
    cli.write_text(f"#!/bin/sh\ncat >/dev/null\nprintf '{body}STATUS: FINDINGS {findings}\\n'\n")
    cli.chmod(cli.stat().st_mode | stat.S_IXUSR)
    run_cli(repo, "init")
    (repo / ".ddflow" / "config.toml").write_text(
        "[worktree]\nenabled = false\n"
        f'[[reviewer]]\nname = "fake"\nkind = "command"\ncommand = "{cli}"\n'
        'model = "gemini-2.5-pro"\ngates = ["critic", "rubber_duck"]\n'
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ddflow")
    run_cli(repo, "task", "add", "T1", "--title", "add files", "--globs", "*.py")
    (repo / "x.py").write_text("x = 1\n")


def _gate(repo: Path, gate: str = "critic"):
    return fold(EventLog(repo).read_all(), strict=False).items["T1"].gates[gate]


def test_every_confirmed_finding_settles_despite_the_fix_moving_the_tree(repo, tmp_path):
    _setup(repo, tmp_path)
    for _ in (1, 2):
        assert api.review(repo, gate="critic", item="T1").data["outcome"] == "failed"
    (repo / "x.py").write_text("x = 2  # fixed\n")  # the fix moves the reviewed tree
    out = api.triage(repo, "T1", gate="critic", finding=1, verdict="confirmed", probe="t passes")
    assert out.exit == OK, out.reason
    assert out.data["passed_on_refutation"] is False
    rec = _gate(repo)
    assert rec.outcome == "passed"
    assert "passed_on_refutation" not in rec.evidence
    assert rec.evidence["findings_fixed"] == {"confirmed": 1, "rounds": 2, "max_rounds": 2}


def test_mixed_refuted_and_confirmed_settle_despite_the_fix_moving_the_tree(repo, tmp_path):
    _setup(repo, tmp_path, findings=2)
    for _ in (1, 2):
        assert api.review(repo, gate="critic", item="T1").data["outcome"] == "failed"
    (repo / "x.py").write_text("x = 2  # fixed finding 1\n")
    first = api.triage(repo, "T1", gate="critic", finding=1, verdict="confirmed", probe="t passes")
    assert first.exit == OK and first.data["passed_on_refutation"] is False
    last = api.triage(repo, "T1", gate="critic", finding=2, verdict="refuted", probe="ran it: ok")
    assert last.exit == OK, last.reason
    assert last.data["passed_on_refutation"] is True
    rec = _gate(repo)
    assert rec.outcome == "passed"
    flag = rec.evidence["passed_on_refutation"]
    assert (flag["refuted"], flag["confirmed"]) == (1, 1)


def test_refutations_over_a_moved_tree_still_do_not_pass(repo, tmp_path):
    """B835d22b138 holds: with no confirmed finding the fix cannot explain the move, so
    the stale round still blocks a pass-on-refutation once the budget is spent."""
    _setup(repo, tmp_path)
    for _ in (1, 2):
        assert api.review(repo, gate="critic", item="T1").data["outcome"] == "failed"
    (repo / "x.py").write_text("x = 2  # edited after the review\n")
    out = api.triage(repo, "T1", gate="critic", finding=1, verdict="refuted", probe="ran it: ok")
    assert out.exit == OK and out.data["passed_on_refutation"] is False
    assert _gate(repo).outcome == "failed", out.data["text"]
    assert "holds other content" in out.data["text"]
