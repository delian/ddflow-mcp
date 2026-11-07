"""B-gates-survive-lost-branch: research/rules outlive a branch that was deleted and rebuilt.

A park deletes the item's worktree and branch; a fresh claim then builds a new tree from
main. research and rules passes from the lost branch kept showing green ``[x]``: gate
staleness compared content only, and only for gates that must attach evidence, so a pass
that had merely outlived its branch read as a pass about the new one. Such a pass is not
older work on the same branch -- its commit is a DESCENDANT of the new tree, not its
ancestor -- so it is stale, not green.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import run_cli
from helpers import git as _git

from ddflow.config import Config
from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.services.gates import outcomes


@pytest.fixture
def adopted(repo: Path) -> Path:
    """A repo + task T1, with the passes recorded on the item's branch AFTER its work."""
    run_cli(repo, "init")
    (repo / "a.py").write_text("a = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ddflow")
    run_cli(repo, "task", "add", "T1", "--title", "T1 fixture", "--globs", "a.py")
    # The item's own branch works on top of main.
    (repo / "a.py").write_text("the lost branch's work\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "work on the item branch")
    work = _git(repo, "rev-parse", "HEAD").strip()
    for gate in ("research", "rules"):
        EventLog(repo, "agent-x").append(
            "gate.passed",
            "T1",
            {
                "gate": gate,
                "by": "agent-x",
                "reason": "",
                "evidence": {"tree_sha": f"{work}+clean"},
            },
        )
    return repo


def _stale(repo: Path, head: str):
    st = fold(EventLog(repo).read_all(), strict=False)
    return outcomes.stale_evidence_detail(st, Config.load(repo), "T1", repo, landed=head)


def test_research_and_rules_are_stale_when_the_branch_was_rebuilt(adopted):
    # The item branch (and the passes it holds) is gone; a fresh claim builds the tree
    # from main, which is the ANCESTOR of the lost branch's commit, so the recorded
    # commit does not descend from the tree being checked.
    main = _git(adopted, "rev-parse", "HEAD~1").strip()
    notes = _stale(adopted, main)
    marked = {n.gate for n in notes}
    assert "research" in marked, "research survived the rebuilt branch as a bare pass"
    assert "rules" in marked, "rules survived the rebuilt branch as a bare pass"
    assert all("rebuilt" in n.why for n in notes), "the note names the branch being rebuilt"


def test_research_and_rules_stay_fresh_within_the_same_branch(adopted):
    # A normal commit on the SAME branch keeps its history: the pass is still this claim's
    # -- research and rules precede the edits that follow, and their commit is an ancestor.
    (adopted / "a.py").write_text("a = 2\n")
    _git(adopted, "add", "-A")
    _git(adopted, "commit", "-qm", "more work")
    head = _git(adopted, "rev-parse", "HEAD").strip()
    assert _stale(adopted, head) == []
