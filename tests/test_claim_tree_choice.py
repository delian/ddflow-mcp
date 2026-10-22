"""A claim says WHICH tree it bound and WHY (B-claim-points-to-ddflow-worktrees-not-session).

The rule never changed: a caller standing in a linked worktree that no other item holds has
it adopted; anyone else (the primary checkout, `adopt_existing` off, a tree someone else is
working in) gets a tree of its own under .ddflow/worktrees. It differed by caller with
nothing said, so a session that got a second tree was not told why.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from helpers import git_raw as _git

from ddflow.api import lifecycle as LC
from ddflow.config import Config
from ddflow.infra.log import EventLog


def _seed(repo: Path) -> Path:
    run_cli(repo, "init")
    (repo / "README.md").write_text("x\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init")
    log = EventLog(repo, "seed")
    log.append("phase.added", "P1", {"title": "p"})
    log.append("task.added", "T1", {"parent": "P1", "title": "t", "globs": ["a.py"]})
    tree = repo.parent / "harness"
    _git(repo, "worktree", "add", "-q", "-b", "harness", str(tree))
    return tree


def test_from_the_primary_checkout_a_tree_is_created_and_the_answer_says_why(repo):
    _seed(repo)
    out = LC.claim(repo, "T1", agent="a", called_from=repo)
    assert out.exit == 0, out.reason
    assert out.data["tree"]["how"] == "created"
    assert "primary" in out.data["tree"]["why"], out.data["tree"]
    assert Path(out.data["worktree"]) == repo / ".ddflow" / "worktrees" / "T1"


def test_from_a_free_linked_worktree_it_is_adopted(repo):
    tree = _seed(repo)
    out = LC.claim(repo, "T1", agent="a", called_from=tree)
    assert out.exit == 0, out.reason
    assert out.data["tree"]["how"] == "adopted"
    assert "no other item holds" in out.data["tree"]["why"]
    assert str(tree.resolve()) == str(Path(out.data["worktree"]).resolve())


def test_adopt_existing_off_creates_a_tree_and_names_the_knob(repo, monkeypatch):
    tree = _seed(repo)
    real = Config.load

    def load(r, *a, **k):
        cfg = real(r, *a, **k)
        cfg.worktree.adopt_existing = False
        return cfg

    monkeypatch.setattr(Config, "load", staticmethod(load))
    out = LC.claim(repo, "T1", agent="a", called_from=tree)
    assert out.exit == 0, out.reason
    assert out.data["tree"]["how"] == "created"
    assert "adopt_existing" in out.data["tree"]["why"], out.data["tree"]
    assert Path(out.data["worktree"]) == repo / ".ddflow" / "worktrees" / "T1"


def test_no_worktree_and_a_second_claim_say_so(repo):
    tree = _seed(repo)
    out = LC.claim(repo, "T1", agent="a", no_worktree=True)
    assert out.data["tree"]["how"] == "none"
    assert "--no-worktree" in out.data["tree"]["why"]
    LC.release(repo, "T1", agent="a")
    first = LC.claim(repo, "T1", agent="a", called_from=repo)
    again = LC.claim(repo, "T1", agent="a", called_from=tree)
    assert first.data["tree"]["how"] == "created"
    assert again.data["tree"]["how"] == "rebound", again.data["tree"]
    assert "earlier claim" in again.data["tree"]["why"]
    assert again.data["worktree"] == first.data["worktree"]


def test_the_cli_names_the_reason_for_a_new_tree(repo):
    _seed(repo)
    code, out, err = run_cli(repo, "claim", "T1", agent="a")
    assert code == 0, out + err
    assert "a tree of its own" in out and "primary" in out, out


def test_the_cli_from_a_free_linked_worktree_adopts_it_and_says_so(repo):
    import os
    import subprocess

    tree = _seed(repo)
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
    env.pop("DDFLOW_AGENT", None)
    p = subprocess.run(
        [sys.executable, "-m", "ddflow", "--agent", "a", "claim", "T1"],
        cwd=tree,
        capture_output=True,
        text=True,
        env=env,
        timeout=300,
    )
    assert p.returncode == 0, p.stdout + p.stderr
    assert "adopted" in p.stdout and "a tree of its own" not in p.stdout, p.stdout
    assert str(tree.resolve()) in p.stdout, p.stdout


def test_the_cli_names_the_items_own_tree_on_a_second_claim(repo):
    _seed(repo)
    assert run_cli(repo, "claim", "T1", agent="a")[0] == 0
    code, out, err = run_cli(repo, "claim", "T1", agent="a")
    assert code == 0, out + err
    assert "the item's own tree, from an earlier claim" in out and ".ddflow/worktrees/T1" in out, (
        out
    )
