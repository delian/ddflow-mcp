"""DX-gate-run-after-merge, B430dae3a68: `merge` removes the worktree, so a command gate run
on a merged item used to have no tree and could only record UNAVAILABLE ("gates are run
before `ddflow merge`"), stranding the item: complete refused forever on a required gate
that no agent word could honestly fill post-merge. The merged work is committed at the
base's HEAD, so the primary IS the item's post-merge tree: `item_tree` answers the
primary for a merged item, and gates genuinely re-run and record there."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from conftest import run_cli
from helpers import git as _git

from ddflow.core.model import fold
from ddflow.infra.log import EventLog


@pytest.fixture(autouse=True)
def _a_person_records_by_hand(monkeypatch):
    """These tests hand-record command gates as a person: an agent's typed pass is refused
    (Bed643a611b), and the suite itself may run under an agent harness."""
    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.delenv("DDFLOW_AGENT", raising=False)


def _merged(repo: Path, *, passed_before: bool = False) -> None:
    run_cli(repo, "init")
    cfg = repo / ".ddflow" / "config.toml"
    cfg.write_text(cfg.read_text() + '\n[gate.unit_tests]\ncommand = "true"\n')
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "cfg")
    run_cli(repo, "task", "add", "T1", "--title", "t")
    code, out, err = run_cli(repo, "--json", "claim", "T1")
    assert code == 0, out + err
    tree = Path(json.loads(out)["worktree"])
    (tree / "t.txt").write_text("x\n")
    _git(tree, "add", ".")
    _git(tree, "commit", "-qm", "work")
    if passed_before:
        run_cli(
            repo, "gate", "record", "T1", "unit_tests", "--outcome", "passed", "--evidence", "e"
        )
    assert run_cli(repo, "merge", "T1")[0] == 0


def _gate(repo: Path, name: str):
    return fold(EventLog(repo).read_all(), strict=False).items["T1"].gates.get(name)


def test_a_gate_run_after_merge_runs_on_the_merged_tree(repo):
    """B430dae3a68: a merged item with NO unit_tests outcome is not stranded: the gate
    genuinely re-runs on the merged tree (the primary, at the base's HEAD) and records
    its pass, so `complete` has an honest post-merge route."""
    _merged(repo)
    code, out, err = run_cli(repo, "gate", "run", "T1", "unit_tests")
    text = out + err
    assert code == 0, text
    rec = _gate(repo, "unit_tests")
    assert rec.outcome == "passed", rec
    assert "unavailable" not in text


def test_a_gate_run_after_merge_reruns_an_existing_pre_merge_pass(repo):
    """The merged tree is the outcome home, so a re-run measures it freshly instead of
    parrot-backing the pre-merge pass."""
    _merged(repo, passed_before=True)
    code, out, err = run_cli(repo, "gate", "run", "T1", "unit_tests")
    text = out + err
    assert code == 0, text
    assert _gate(repo, "unit_tests").outcome == "passed"


def test_a_forced_gate_run_after_merge_takes_the_fresh_measurement(repo):
    """--force asks for a fresh measurement; on the merged tree there now IS one to take,
    so the pre-merge pass is superseded by a fresh run rather than replaced by
    unavailable."""
    _merged(repo, passed_before=True)
    code, out, err = run_cli(
        repo, "gate", "run", "T1", "unit_tests", "--force", "--reason", "fresh please"
    )
    text = out + err
    assert code == 0, text
    assert _gate(repo, "unit_tests").outcome == "passed"


def test_a_gate_run_after_a_later_commit_does_not_claim_the_moved_primary(repo):
    """B430dae3a68 holefix: `merged_sha` being in the base's history says the merge was
    landed, not that the primary is checked out AT it. A later commit moves the primary
    past the merge; the gate must not measure that tree as T1's merged one and record
    its pass for T1."""
    _merged(repo)
    (repo / "later.txt").write_text("later\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "later work on main")
    code, out, err = run_cli(repo, "gate", "run", "T1", "unit_tests")
    text = out + err
    assert code != 0, text
    assert _gate(repo, "unit_tests").outcome == "unavailable"


def test_a_gate_run_after_a_revert_does_not_claim_the_reverted_primary(repo):
    """A revert keeps `merged_sha` in main's history while the checked-out tree no longer
    holds T1's work. The ancestry check alone passes; only the tree comparison may admit
    the primary as the merged tree (B430dae3a68 holefix)."""
    _merged(repo)
    # `merge` leaves its own event-log commit on top: revert the MERGE commit beneath it
    merge_commit = _git(repo, "log", "--merges", "-1", "--format=%H").strip()
    _git(repo, "revert", "--no-edit", "-m", "1", merge_commit)
    code, out, err = run_cli(repo, "gate", "run", "T1", "unit_tests")
    text = out + err
    assert code != 0, text
    assert _gate(repo, "unit_tests").outcome == "unavailable"


def test_a_dirty_primary_is_not_the_merged_tree(repo):
    """`HEAD^{tree}` sees only the committed tree: a merged item at the merged tree with
    an uncommitted edit is not at the merged tree either. The reviewers pinned this tail
    of the hole, and it is the same wrong-tree admission (B430dae3a68 holefix)."""
    _merged(repo)
    (repo / "t.txt").write_text("dirty\n")
    code, out, err = run_cli(repo, "gate", "run", "T1", "unit_tests")
    text = out + err
    assert code != 0, text
    assert _gate(repo, "unit_tests").outcome == "unavailable"


def test_a_hidden_marked_primary_is_not_the_merged_tree(repo):
    """A tracked file the index marks `assume-unchanged` (or `skip-worktree`) and then
    modifies is INVISIBLE to `git status` and `git diff --quiet` while its content
    differs, so no checkout whose index carries a mark can be PROVEN to stand at the
    merged tree and none is admitted (B430dae3a68 reviewer findings #1/#3)."""
    _merged(repo)
    _git(repo, "update-index", "--assume-unchanged", "t.txt")
    (repo / "t.txt").write_text("hidden edit under the mark\n")
    _git(repo, "update-index", "--skip-worktree", "t.txt")
    code, out, err = run_cli(repo, "gate", "run", "T1", "unit_tests")
    text = out + err
    assert code != 0, text
    assert _gate(repo, "unit_tests").outcome == "unavailable"


def test_a_merged_phase_off_the_merged_tree_is_implement_judged(repo):
    """The reviewers' fourth round: the merged-phase implement exemption skipped the
    merged-tree test, so a phase whose checkout moved off its merged work recorded
    `implement` passed where a task is refused. The exemption holds only where the
    checkout stands at the merged tree (the landed case, judged for every kind);
    off it the branch is measured, and an empty range is refused (finding #2)."""
    run_cli(repo, "init")
    run_cli(repo, "config", "worktree.remove_on_merge", "false")
    run_cli(repo, "phase", "add", "P1", "--title", "p")
    code, out, err = run_cli(repo, "--json", "claim", "P1")
    assert code == 0, out + err
    tree = Path(json.loads(out)["worktree"])
    (tree / "p.txt").write_text("x\n")
    _git(tree, "add", ".")
    _git(tree, "commit", "-qm", "work")
    assert run_cli(repo, "merge", "P1")[0] == 0
    _git(tree, "checkout", "--detach", "HEAD~1")
    (repo / "later.txt").write_text("later\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "later work on main")
    code, out, err = run_cli(
        repo, "gate", "record", "P1", "implement", "--outcome", "passed", "--evidence", "x"
    )
    assert code == 3 and "no commit ahead" in out + err, (code, out, err)


def test_a_landed_item_kept_worktree_is_exempt_only_where_the_checkout_stands_at_it(repo):
    """The second site of the same hole (`_implement_without_commits`): a landed item whose
    worktree a merge kept was judged "it IS the base" from the ancestry alone. With the
    checkout moved off its merged work, `implement` must measure the branch, not take
    `merged_sha`'s ancestry as the checkout's."""
    run_cli(repo, "init")
    run_cli(repo, "config", "worktree.remove_on_merge", "false")
    run_cli(repo, "task", "add", "T1", "--title", "t")
    code, out, err = run_cli(repo, "--json", "claim", "T1")
    assert code == 0, out + err
    tree = Path(json.loads(out)["worktree"])
    (tree / "t.txt").write_text("x\n")
    _git(tree, "add", ".")
    _git(tree, "commit", "-qm", "work")
    assert run_cli(repo, "merge", "T1")[0] == 0
    _git(tree, "checkout", "--detach", "HEAD~1")
    code, out, err = run_cli(
        repo, "gate", "record", "T1", "implement", "--outcome", "passed", "--evidence", "x"
    )
    assert code == 3 and "no commit ahead" in out + err, (code, out, err)


def test_a_kept_worktree_off_the_merged_tree_is_not_the_item_tree(repo):
    """The kept-worktree tail of the hole: `item_tree` answered a merged item's recorded
    worktree path without asking `_at_merged_tree`, so a KEPT (remove_on_merge=false) tree
    moved off the merged work was still measured and recorded for the merged item. Only a
    checkout standing AT the merged tree counts (B430dae3a68 holefix, review round 3)."""
    run_cli(repo, "init")
    cfg = repo / ".ddflow" / "config.toml"
    cfg.write_text(cfg.read_text() + '\n[gate.unit_tests]\ncommand = "true"\n')
    run_cli(repo, "config", "worktree.remove_on_merge", "false")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "cfg")
    run_cli(repo, "task", "add", "T1", "--title", "t")
    code, out, err = run_cli(repo, "--json", "claim", "T1")
    assert code == 0, out + err
    tree = Path(json.loads(out)["worktree"])
    (tree / "t.txt").write_text("x\n")
    _git(tree, "add", ".")
    _git(tree, "commit", "-qm", "work")
    assert run_cli(repo, "merge", "T1")[0] == 0
    _git(tree, "checkout", "--detach", "HEAD~1")
    (repo / "later.txt").write_text("later\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "later work on main")
    code, out, err = run_cli(repo, "gate", "run", "T1", "unit_tests")
    text = out + err
    assert code != 0, text
    assert _gate(repo, "unit_tests").outcome == "unavailable"
