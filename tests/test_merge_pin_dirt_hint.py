"""B8f12fe0f03: a merge into the primary refused because a stray `ddflow pins regenerate` left
a pin file (README.md) uncommitted there, and the refusal did not say who owned that dirt."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_flow import _commit, _git

from ddflow.config import Config
from ddflow.infra import worktree as W
from ddflow.services import pins_dirt as PN

RECIPES = '[[pin]]\nname = "x"\nfiles = ["README.md"]\ncheck = ["true"]\nregen = ["true"]\n'


def _refused(repo, name: str):
    (repo / ".ddflow").mkdir(exist_ok=True)
    (repo / ".ddflow" / "pins.toml").write_text(RECIPES)
    cfg = Config()
    _commit(repo, name, "tracked\n", "add " + name)
    wt = W.create(repo, cfg, "T1")
    _commit(wt.path, name, "from the branch\n", "feat: touch")
    _commit(repo, "g.py", "y\n", "main moves")
    (repo / name).write_text("stray local change\n")
    r = W.merge(repo, cfg, wt)
    assert not r.ok
    return cfg, r


def test_a_dirty_pin_file_in_the_primary_is_named_with_its_owner_and_remedy(repo):
    cfg, r = _refused(repo, "README.md")
    hint = PN.dirt_hint(repo, cfg, r.err or r.out, repo)
    assert "README.md is a pin file" in hint
    assert "ddflow pins regenerate" in hint
    assert f"git -C {repo} checkout HEAD -- README.md" in hint


def test_a_dirty_file_that_is_no_pin_gets_no_pin_advice(repo):
    cfg, r = _refused(repo, "notes.txt")
    assert PN.dirt_hint(repo, cfg, r.err or r.out, repo) == ""


def test_ddflow_merge_refusal_carries_the_hint(repo):
    from conftest import run_cli

    run_cli(repo, "init")
    (repo / ".ddflow" / "pins.toml").write_text(RECIPES)
    (repo / "README.md").write_text("tracked\n")
    _git(repo, "add", "-A")
    _git(repo, "-c", "commit.gpgsign=false", "commit", "-qm", "readme", "--no-verify")
    run_cli(repo, "workflow", "pipeline", "task", "implement,merge")
    run_cli(repo, "task", "add", "T1", "--globs", "README.md")
    code, _o, err = run_cli(repo, "claim", "T1")
    assert code == 0, err
    wt = repo / ".ddflow" / "worktrees" / "T1"
    _commit(wt, "README.md", "from the branch\n", "feat: touch")
    run_cli(repo, "gate", "record", "T1", "implement", "--outcome", "passed")
    (repo / "README.md").write_text("stray local change\n")
    code, out, err = run_cli(repo, "merge", "T1")
    assert code != 0
    assert "README.md is a pin file" in out + err
