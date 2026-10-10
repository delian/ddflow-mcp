"""Which commit says an item shipped on a line.

B9a337697c0: `merge` came to record the LANDING commit as `merged_sha` (B9f8019c521, so
a caller cites the commit that is on the base). Version planning asked "is merged_sha an
ancestor of this line?" -- and a gitflow hotfix's landing is main's merge commit, which
never reaches develop: the branch head does, by back-merge. So the hotfix vanished from
develop's version plan and its bump. Either commit reaching the line ships the item.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import pass_pipeline, run_cli
from helpers import git as _git

from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.surfaces import mcp
from ddflow.surfaces.declared.lifecycle_cli import MERGE_PAYLOAD


@pytest.fixture
def gitflow(repo, monkeypatch):
    monkeypatch.delenv("DDFLOW_AGENT", raising=False)
    _git(repo, "branch", "develop")
    _git(repo, "checkout", "-q", "develop")
    run_cli(repo, "init")
    with (repo / ".ddflow" / "config.toml").open("a") as fh:
        fh.write('\n[flow]\nmodel = "gitflow"\n')
    _git(repo, "add", ".ddflow")
    _git(repo, "commit", "-qm", "chore: adopt")
    return repo


def test_a_back_merged_hotfix_ships_on_develop(gitflow):
    repo = gitflow
    run_cli(repo, "task", "add", "H1", "--tags", "hotfix")
    code, out, err = run_cli(repo, "--json", "claim", "H1")
    assert code == 0, out + err
    tree = Path(json.loads(out)["worktree"])
    (tree / "fix.py").write_text("fixed\n")
    _git(tree, "add", "fix.py")
    _git(tree, "commit", "-qm", "fix: the outage")
    head = _git(tree, "rev-parse", "HEAD")
    code, out, err = run_cli(repo, "merge", "H1")
    assert code == 0, out + err
    pass_pipeline(repo, "H1")
    code, out, err = run_cli(repo, "complete", "H1", "--model", "claude-opus-5")
    assert code == 0, out + err

    it = fold(EventLog(repo).read_all(), strict=False).items["H1"]
    assert it.merged_sha == _git(repo, "rev-parse", "main"), "the landing is main's"
    assert head != it.merged_sha
    _git(repo, "merge-base", "--is-ancestor", head, "develop")  # back-merged: raises if not

    code, out, err = run_cli(repo, "--json", "version", "show")
    assert code == 0, out + err
    assert "H1" in json.loads(out)["items"], out


def test_the_merge_body_is_the_same_on_both_surfaces():
    assert tuple(mcp.TOOLS["ddflow_merge"]["payload"]) == MERGE_PAYLOAD


def test_an_old_branch_head_record_is_not_shipped_by_its_own_second_parent(repo):
    """Before B9f8019c521 merged_sha was the branch head. When that branch had merged
    develop into itself, its second parent is a develop commit -- which says nothing
    about the item reaching develop."""
    from ddflow.config import Config
    from ddflow.core.model import Item
    from ddflow.services.flow import reached

    base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-qb", "develop")
    (repo / "d.txt").write_text("d\n")
    _git(repo, "add", "d.txt")
    _git(repo, "commit", "-qm", "develop work")
    _git(repo, "checkout", "-qb", "feature", base)
    (repo / "f.txt").write_text("f\n")
    _git(repo, "add", "f.txt")
    _git(repo, "commit", "-qm", "feature work")
    _git(repo, "merge", "-q", "--no-ff", "develop", "-m", "sync develop")
    head = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "-q", "--no-ff", "feature", "-m", "land feature")
    landing = _git(repo, "rev-parse", "HEAD")

    cfg = Config()
    legacy = Item(id="T", kind="task", merged_sha=head, landed_before=base, landed_after=landing)
    assert reached(repo, cfg, legacy, "develop") == ""
    assert reached(repo, cfg, legacy, "main") == head
    current = Item(
        id="T", kind="task", merged_sha=landing, landed_before=base, landed_after=landing
    )
    assert reached(repo, cfg, current, "develop") == ""  # the feature itself never reached it
    assert reached(repo, cfg, current, "main") == landing


def test_a_fast_forward_landing_is_not_shipped_by_what_the_branch_merged_in(repo):
    """ff-only: the landing IS the branch head. A branch that only merged develop in has
    develop's tip as its second parent -- the item still never reached develop."""
    from ddflow.config import Config
    from ddflow.core.model import Item
    from ddflow.services.flow import reached

    base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-qb", "develop")
    (repo / "d.txt").write_text("d\n")
    _git(repo, "add", "d.txt")
    _git(repo, "commit", "-qm", "develop work")
    _git(repo, "checkout", "-qb", "feature", base)
    _git(repo, "merge", "-q", "--no-ff", "develop", "-m", "sync develop")
    head = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "-q", "--ff-only", "feature")
    assert _git(repo, "rev-parse", "main") == head

    cfg = Config()
    cfg.worktree.merge_strategy = "ff-only"
    landed = Item(id="T", kind="task", merged_sha=head, landed_before=base, landed_after=head)
    assert reached(repo, cfg, landed, "develop") == ""
    assert reached(repo, cfg, landed, "main") == head
