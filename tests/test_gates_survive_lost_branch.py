"""B-gates-survive-lost-branch: research/rules outlive a branch that was deleted and rebuilt.

A park deletes the item's worktree and branch; a fresh claim then builds a new tree from
main. research and rules passes from the lost branch kept showing green ``[x]``: gate
staleness compared content only, and only for gates that must attach evidence, so a pass
that had merely outlived its branch read as a pass about the new one. Such a pass is not
older work on the same branch -- its commit is not an ancestor of the new tree -- so it is
stale, not green.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import run_cli
from helpers import git as _git

from ddflow.config import Config
from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.services import gates as G
from ddflow.services.gates import outcomes


@pytest.fixture
def recorded(repo: Path) -> Path:
    run_cli(repo, "init")
    (repo / "a.py").write_text("a = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ddflow")
    run_cli(repo, "task", "add", "T1", "--title", "T1 fixture", "--globs", "a.py")
    for gate in ("research", "rules"):
        EventLog(repo, "agent-x").append(
            "gate.passed",
            "T1",
            {
                "gate": gate,
                "by": "agent-x",
                "reason": "",
                "evidence": {"tree_sha": G.tree_identity(repo)},
            },
        )
    return repo


def _stale(repo: Path, head: str):
    st = fold(EventLog(repo).read_all(), strict=False)
    return outcomes.stale_evidence_detail(st, Config.load(repo), "T1", repo, landed=head)


def test_research_and_rules_are_stale_when_the_branch_was_rebuilt(recorded):
    # The branch the passes were recorded on is gone; a fresh claim built an unrelated tree.
    _git(recorded, "checkout", "-q", "--orphan", "rebuilt")
    _git(recorded, "commit", "-qm", "clean rebuild", "--allow-empty")
    new = _git(recorded, "rev-parse", "HEAD").strip()
    marked = {n.gate for n in _stale(recorded, new)}
    assert "research" in marked, "research survived the rebuilt branch as a bare pass"
    assert "rules" in marked, "rules survived the rebuilt branch as a bare pass"


def test_research_and_rules_stay_fresh_within_the_same_branch(recorded):
    # A normal commit on the SAME branch keeps its history: content moved on, but the
    # pass is still this claim's -- research and rules precede the edits that follow.
    (recorded / "a.py").write_text("a = 2\n")
    _git(recorded, "add", "-A")
    _git(recorded, "commit", "-qm", "work")
    new = _git(recorded, "rev-parse", "HEAD").strip()
    assert _stale(recorded, new) == []
