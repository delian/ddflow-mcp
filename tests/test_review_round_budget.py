"""B-review-round-budget-no-fix-path: a confirmed-and-fixed finding settles a gate whose
round budget is spent, even though the fix moved the tree the round reviewed.

Before the fix, `_settle_after_cap` returned "stays failed" for ANY stale review round once
the budget was spent, so a gate whose findings were ALL confirmed (and fixed) had no path:
the fix moved the item's tree, the settle was blocked as stale, and a re-review was refused
past the cap. A confirmed finding is fixed -- the moved tree IS the fix, not unreviewed work
-- so such a settle records `findings_fixed`. A stale tree still blocks a pass as soon as any
finding was REFUTED (B835d22b138), which the boundary tests lock in unchanged.
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
    """A command reviewer reporting finding ``n`` on ``f{n}.py:1`` for each n, so a test can
    fix one finding's file without touching another's (the item's tree is the uncommitted
    ``f*.py`` files; worktree disabled)."""
    cli = tmp_path / "fake-reviewer"
    body = "".join(f"FINDING HIGH f{n}.py:1\\nproblem {n}\\n\\n" for n in range(1, findings + 1))
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
    for n in range(1, findings + 1):
        (repo / f"f{n}.py").write_text("x = 1\n")


def _gate(repo: Path, gate: str = "critic"):
    return fold(EventLog(repo).read_all(), strict=False).items["T1"].gates[gate]


def test_every_confirmed_finding_settles_despite_the_fix_moving_the_tree(repo, tmp_path):
    _setup(repo, tmp_path)
    for _ in (1, 2):
        assert api.review(repo, gate="critic", item="T1").data["outcome"] == "failed"
    (repo / "f1.py").write_text("x = 2  # fixed\n")  # the fix moves the reviewed tree
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
    _setup(repo, tmp_path, findings=2)
    for _ in (1, 2):
        assert api.review(repo, gate="critic", item="T1").data["outcome"] == "failed"
    (repo / "f1.py").write_text("x = 2  # fixed finding 1\n")
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
    _setup(repo, tmp_path)
    for _ in (1, 2):
        assert api.review(repo, gate="critic", item="T1").data["outcome"] == "failed"
    (repo / "f1.py").write_text("x = 2  # edited after the review\n")
    out = api.triage(repo, "T1", gate="critic", finding=1, verdict="refuted", probe="ran it: ok")
    assert out.exit == OK and out.data["passed_on_refutation"] is False
    assert _gate(repo).outcome == "failed", out.data["text"]
    assert "holds other content" in out.data["text"]


def test_a_refutation_blocks_over_a_refuted_files_own_move_even_mixed_with_a_confirmed_fix(
    repo, tmp_path
):
    """The relax is tied to the CONFIRMED findings' files only: with finding 1 confirmed and
    fixed (f1.py) and finding 2 refuted on f2.py, a movement that includes f2.py (the
    refuted finding's own file) is unreviewed content, not the confirmed fix, so the stale
    block stays. The attribution check is pinned at the helper level because the
    worktree-disabled `_setup` records no `reviewed_paths` (its item has no worktree/branch),
    so the end-to-end settle sees `moved=None` and keeps the old fallback."""
    from ddflow.api.review.triaging import _confirmed_files, _movement_is_confirmed_fix
    from ddflow.services.gates import outcomes as GD

    _setup(repo, tmp_path, findings=2)
    for _ in (1, 2):
        assert api.review(repo, gate="critic", item="T1").data["outcome"] == "failed"
    (repo / "f1.py").write_text("x = 2  # fixed finding 1\n")
    api.triage(repo, "T1", gate="critic", finding=1, verdict="confirmed", probe="f1 fixed")
    api.triage(repo, "T1", gate="critic", finding=2, verdict="refuted", probe="ran it: ok")
    st = fold(EventLog(repo).read_all(), strict=False)
    it = st.items["T1"]
    counts = GD.triage_counts(it, "critic") or {}
    assert _confirmed_files(it, "critic") == {"f1.py"}
    # The fix alone moved the tree: attributable, so the stale is explained.
    assert _movement_is_confirmed_fix(counts, ["f1.py"], it, "critic") is True
    # The refuted finding's own file moved too: NOT the confirmed fix, so it stays stale.
    assert _movement_is_confirmed_fix(counts, ["f1.py", "f2.py"], it, "critic") is False


def test_an_unsettled_finding_never_settles_a_stale_round(repo, tmp_path):
    """A confirmed finding does not bypass the untriaged hold: with a second finding left
    untriaged, the spend-budget settle still names 'findings have no verdict'."""
    _setup(repo, tmp_path, findings=2)
    for _ in (1, 2):
        assert api.review(repo, gate="critic", item="T1").data["outcome"] == "failed"
    (repo / "f1.py").write_text("x = 2  # fixed\n")
    out = api.triage(repo, "T1", gate="critic", finding=1, verdict="confirmed", probe="t passes")
    assert out.exit == OK and out.data["passed_on_refutation"] is False
    assert "have no verdict" in out.data["text"]
    assert _gate(repo).outcome == "failed"
