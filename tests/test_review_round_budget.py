"""B-review-round-budget-no-fix-path: a confirmed-and-fixed finding settles a gate whose
round budget is spent, even though the fix moved the tree the round reviewed.

Before the fix, `_settle_after_cap` returned "stays failed" for ANY stale review round once
the budget was spent, so a gate whose findings were ALL confirmed (and fixed) had no path:
the fix moved the item's tree, the settle was blocked as stale, and a re-review was refused
past the cap. A confirmed finding is fixed -- the moved tree IS the fix, not unreviewed work
-- so such a settle records `findings_fixed`. A stale tree still blocks a pass as soon as any
finding was REFUTED (B835d22b138), or when the moved files are not the confirmed findings'
(the refuted finding's own move is unreviewed content), which the boundary tests lock in.
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


def _setup(repo: Path, tmp_path: Path, findings: int = 1) -> Path:
    """A command reviewer reporting finding ``n`` on ``f{n}.py:1`` for each n, so a test can
    fix one finding's file without touching another's. The item is claimed (worktree on) and
    its tree is the uncommitted ``f*.py`` files in that worktree, so a review records
    `reviewed_paths` and a triage can tell the confirmed fix moved the tree. Returns the
    worktree."""
    cli = tmp_path / "fake-reviewer"
    body = "".join(f"FINDING HIGH f{n}.py:1\\nproblem {n}\\n\\n" for n in range(1, findings + 1))
    cli.write_text(f"#!/bin/sh\ncat >/dev/null\nprintf '{body}STATUS: FINDINGS {findings}\\n'\n")
    cli.chmod(cli.stat().st_mode | stat.S_IXUSR)
    run_cli(repo, "init")
    (repo / ".ddflow" / "config.toml").write_text(
        f'[[reviewer]]\nname = "fake"\nkind = "command"\ncommand = "{cli}"\n'
        'model = "gemini-2.5-pro"\ngates = ["critic", "rubber_duck"]\n'
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ddflow")
    run_cli(repo, "task", "add", "T1", "--title", "add files", "--globs", "*.py")
    run_cli(repo, "claim", "T1")
    wt = repo / ".ddflow/worktrees/T1"
    for n in range(1, findings + 1):
        (wt / f"f{n}.py").write_text("x = 1\n")
    _git(wt, "add", "-A")
    _git(wt, "commit", "-qm", "work")
    return wt


def _gate(repo: Path, gate: str = "critic"):
    return fold(EventLog(repo).read_all(), strict=False).items["T1"].gates[gate]


def test_every_confirmed_finding_settles_despite_the_fix_moving_the_tree(repo, tmp_path):
    wt = _setup(repo, tmp_path)
    for _ in (1, 2):
        assert api.review(repo, gate="critic", item="T1").data["outcome"] == "failed"
    (wt / "f1.py").write_text("x = 2  # fixed\n")  # the fix moves the reviewed tree
    _git(wt, "add", "-A")
    _git(wt, "commit", "-qm", "fix")
    out = api.triage(repo, "T1", gate="critic", finding=1, verdict="confirmed", probe="t passes")
    assert out.exit == OK, out.reason
    assert out.data["passed_on_refutation"] is False
    rec = _gate(repo)
    assert rec.outcome == "passed"
    assert "passed_on_refutation" not in rec.evidence
    assert rec.evidence["findings_fixed"] == {"confirmed": 1, "rounds": 2, "max_rounds": 2}


def test_mixed_refuted_and_confirmed_settle_despite_the_fix_moving_the_tree(repo, tmp_path):
    """The relaxed settle is for any gate with a confirmed (fixed) finding, mixed included:
    the fix moved finding 1's file (confirmed), finding 2 was refuted on an untouched file,
    and the gate settles once its budget is spent (the B-review-round-budget scenario of a
    gate of refuted + confirmed-and-fixed findings over a moved tree)."""
    wt = _setup(repo, tmp_path, findings=2)
    for _ in (1, 2):
        assert api.review(repo, gate="critic", item="T1").data["outcome"] == "failed"
    (wt / "f1.py").write_text("x = 2  # fixed finding 1\n")
    _git(wt, "add", "-A")
    _git(wt, "commit", "-qm", "fix")
    first = api.triage(repo, "T1", gate="critic", finding=1, verdict="confirmed", probe="t passes")
    assert first.exit == OK and first.data["passed_on_refutation"] is False
    last = api.triage(repo, "T1", gate="critic", finding=2, verdict="refuted", probe="ran it: ok")
    assert last.exit == OK, last.reason
    assert last.data["passed_on_refutation"] is True
    rec = _gate(repo)
    assert rec.outcome == "passed", last.data["text"]
    flag = rec.evidence["passed_on_refutation"]
    assert (flag["refuted"], flag["confirmed"]) == (1, 1)


def test_refutations_over_a_moved_tree_still_do_not_pass(repo, tmp_path):
    wt = _setup(repo, tmp_path)
    for _ in (1, 2):
        assert api.review(repo, gate="critic", item="T1").data["outcome"] == "failed"
    (wt / "f1.py").write_text("x = 2  # edited after the review\n")
    _git(wt, "add", "-A")
    _git(wt, "commit", "-qm", "edit")
    out = api.triage(repo, "T1", gate="critic", finding=1, verdict="refuted", probe="ran it: ok")
    assert out.exit == OK and out.data["passed_on_refutation"] is False
    assert _gate(repo).outcome == "failed", out.data["text"]
    assert "holds other content" in out.data["text"]


def test_a_refutation_blocks_over_a_refuted_files_own_move_even_mixed_with_a_confirmed_fix(
    repo, tmp_path
):
    """The relax is tied to the CONFIRMED findings' files only: with finding 1 confirmed and
    fixed (f1.py) and finding 2 refuted on f2.py, moving f2.py for its own reason is
    unreviewed content, so the gate stays failed instead of settling over it."""
    wt = _setup(repo, tmp_path, findings=2)
    for _ in (1, 2):
        assert api.review(repo, gate="critic", item="T1").data["outcome"] == "failed"
    (wt / "f1.py").write_text("x = 2  # fixed finding 1\n")
    (wt / "f2.py").write_text("x = 3  # moved for its own reason\n")
    _git(wt, "add", "-A")
    _git(wt, "commit", "-qm", "fix")
    first = api.triage(repo, "T1", gate="critic", finding=1, verdict="confirmed", probe="t passes")
    assert first.exit == OK and first.data["passed_on_refutation"] is False
    last = api.triage(repo, "T1", gate="critic", finding=2, verdict="refuted", probe="ran it: ok")
    assert last.exit == OK, last.reason
    assert last.data["passed_on_refutation"] is False
    assert _gate(repo).outcome == "failed", last.data["text"]
    assert "holds other content" in last.data["text"]


def test_an_unsettled_finding_never_settles_a_stale_round(repo, tmp_path):
    """A confirmed finding does not bypass the untriaged hold: with a second finding left
    untriaged, the spend-budget settle still names 'findings have no verdict'."""
    wt = _setup(repo, tmp_path, findings=2)
    for _ in (1, 2):
        assert api.review(repo, gate="critic", item="T1").data["outcome"] == "failed"
    (wt / "f1.py").write_text("x = 2  # fixed\n")
    _git(wt, "add", "-A")
    _git(wt, "commit", "-qm", "fix")
    out = api.triage(repo, "T1", gate="critic", finding=1, verdict="confirmed", probe="t passes")
    assert out.exit == OK and out.data["passed_on_refutation"] is False
    assert "have no verdict" in out.data["text"]
    assert _gate(repo).outcome == "failed"
