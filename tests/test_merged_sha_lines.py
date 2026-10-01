"""Which commit says an item shipped on a line.

B9a337697c0: `merge` came to record the LANDING commit as `merged_sha` (B9f8019c521, so
a caller cites the commit that is on the base). Version planning asked "is merged_sha an
ancestor of this line?" -- and a gitflow hotfix's landing is main's merge commit, which
never reaches develop: the branch head does, by back-merge. So the hotfix vanished from
develop's version plan and its bump. Either commit reaching the line ships the item.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import pass_pipeline, run_cli

from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.surfaces import mcp
from ddflow.surfaces.commands.lifecycle import MERGE_PAYLOAD


def _git(where: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(where), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


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
