"""A command gate runs where the item's work is, or records unavailable (B8be9373cf5).

`gate run` ran a task's `unit_tests` in the item's tree -- and, for an item claimed
`--no-worktree`, in the PRIMARY checkout instead: the suite of `main`, recorded as the
change's pass (B-no-worktree-lifecycle.unit_tests = passed, diff_stat files=0). A task's
work is in its tree, else in the tree the caller stands in; from the primary there is
nothing of the item's to run, and that is unavailable, not a pass.

A PHASE has no tree by design -- its gates test the merged result -- and a project with
worktrees switched off works in the primary: both still run there.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

OK, FAIL, NOTHING, REFUSED = 0, 1, 2, 3

# Passes only where the item's change is: a.py exists on its branch and nowhere else.
PROBE = f"{sys.executable} -c \"import pathlib,sys; sys.exit(0 if pathlib.Path('a.py').exists() else 1)\""


def _git(where: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(where), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _setup(repo: Path) -> Path:
    run_cli(repo, "init")
    code, out, err = run_cli(repo, "config", "--set", "gate.unit_tests.command", PROBE)
    assert code == OK, out + err
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ddflow")
    tree = repo.parent / "agent-tree"
    _git(repo, "worktree", "add", "-q", str(tree), "-b", "agent-work")
    run_cli(repo, "task", "add", "T1", "--title", "add a", "--globs", "a.py")
    code, out, err = run_cli(tree, "claim", "T1", "--no-worktree")
    assert code == OK, out + err
    (tree / "a.py").write_text("a = 1\n")
    _git(tree, "add", "a.py")
    _git(tree, "commit", "-qm", "add a")
    return tree


def test_from_the_primary_it_is_unavailable_not_mains_suite(repo):
    _setup(repo)
    code, out, err = run_cli(repo, "--json", "gate", "run", "T1", "unit_tests")
    assert code == NOTHING, out + err
    assert json.loads(out)["outcome"] == "unavailable"


def test_from_the_tree_the_item_is_worked_in_it_runs_there(repo):
    tree = _setup(repo)
    code, out, err = run_cli(tree, "--json", "gate", "run", "T1", "unit_tests")
    assert code == OK, out + err
    assert json.loads(out)["outcome"] == "passed"


def test_a_phase_still_runs_in_the_primary(repo):
    run_cli(repo, "init")
    run_cli(repo, "config", "--set", "gate.unit_tests.command", f"{sys.executable} -c 0")
    run_cli(repo, "phase", "add", "P1", "--title", "p")
    code, out, err = run_cli(repo, "--json", "gate", "run", "P1", "unit_tests")
    assert code == OK, out + err
    assert json.loads(out)["outcome"] == "passed"


def _last_evidence(repo: Path, gate: str) -> dict:
    from ddflow.infra.log import EventLog

    evs = [e for e in EventLog(repo).read_all() if e.kind == "gate.passed"]
    return [e.data for e in evs if e.data.get("gate") == gate][-1].get("evidence", {})


def test_a_recorded_gate_measures_the_items_tree_never_the_primarys(repo):
    """An agent gate is stamped with WHICH tree and HOW MUCH. For an item claimed without
    a tree, that stamp was the primary's -- other agents' uncommitted files, as this
    item's diff. From the primary it now measures nothing; from the tree, the tree."""
    tree = _setup(repo)
    (repo / "README.md").write_text("somebody else's uncommitted change\n")
    args = ("gate", "record", "T1", "research", "--outcome", "passed", "--evidence", "probe")
    assert run_cli(repo, *args)[0] == OK
    assert "diff_stat" not in _last_evidence(repo, "research")
    (tree / "b.py").write_text("b = 1\n")  # the item's own uncommitted work
    assert run_cli(tree, *args)[0] == OK
    assert _last_evidence(repo, "research")["diff_stat"]["untracked"] == 1
