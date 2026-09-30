"""A command gate runs, and a recorded gate is measured, where the item's work is (B8be9373cf5).

For an item claimed `--no-worktree`, `gate run` ran in the PRIMARY checkout even when
called from the worktree the item was worked in: the suite of `main`, recorded as the
change's pass (B-no-worktree-lifecycle.unit_tests = passed, diff_stat files=0). A task's
work is in its tree, else in the linked worktree the caller stands in, else -- for a lone
agent that claimed `--no-worktree` and works in the primary -- in the primary.

A PHASE has no tree by design -- its gates test the merged result -- so it runs there too.
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


def test_claimed_without_a_tree_and_run_from_the_primary_is_unavailable(repo):
    """The primary's working tree is nobody's in particular: any uncommitted edit there
    would be credited to the item (the critic review of the first cut)."""
    _setup(repo)
    code, out, err = run_cli(repo, "--json", "gate", "run", "T1", "unit_tests")
    assert code == NOTHING, out + err
    assert json.loads(out)["outcome"] == "unavailable"


def test_a_lone_agent_working_in_the_primary_says_so_with_worktrees_off(repo):
    run_cli(repo, "init")
    run_cli(repo, "config", "--set", "gate.unit_tests.command", PROBE)
    run_cli(repo, "config", "--set", "worktree.enabled", "false")
    run_cli(repo, "task", "add", "T1", "--title", "add a", "--globs", "a.py")
    assert run_cli(repo, "claim", "T1")[0] == OK
    (repo / "a.py").write_text("a = 1\n")
    code, out, err = run_cli(repo, "--json", "gate", "run", "T1", "unit_tests")
    assert code == OK, out + err
    assert json.loads(out)["outcome"] == "passed"


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


def test_a_recorded_gate_is_measured_in_the_tree_the_caller_stands_in(repo):
    """An agent gate is stamped with WHICH tree and HOW MUCH. For an item claimed without
    a tree, recorded from the tree it is worked in, that stamp was the PRIMARY's."""
    tree = _setup(repo)
    (repo / "README.md").write_text("somebody else's uncommitted change\n")
    (tree / "b.py").write_text("b = 1\n")  # the item's own uncommitted work
    args = ("gate", "record", "T1", "research", "--outcome", "passed", "--evidence", "probe")
    assert run_cli(tree, *args)[0] == OK
    from ddflow.services.gates import diff_stat

    stat = _last_evidence(repo, "research")["diff_stat"]
    assert stat == diff_stat(tree) != diff_stat(repo), stat


def _another_items_tree(repo: Path) -> Path:
    """A second harness tree, adopted by another open item, T2."""
    other = repo.parent / "t2-tree"
    _git(repo, "worktree", "add", "-q", str(other), "-b", "t2-work")
    run_cli(repo, "task", "add", "T2", "--title", "t2", "--globs", "z.py")
    code, out, err = run_cli(other, "claim", "T2")
    assert code == OK and "adopted" in out, out + err
    return other


def test_from_another_items_tree_it_is_unavailable_not_that_items_suite(repo):
    """Found by the critic review of the first fix: standing in item B's tree, item A's
    gate ran B's suite and recorded it as A's pass."""
    _setup(repo)
    other = _another_items_tree(repo)
    (other / "a.py").write_text("a = 1\n")  # would make the probe pass -- in T2's tree
    code, out, err = run_cli(other, "--json", "gate", "run", "T1", "unit_tests")
    assert code == NOTHING, out + err
    assert json.loads(out)["outcome"] == "unavailable"
    rec = json.loads(run_cli(repo, "--json", "show", "T1")[1])["gates"]["unit_tests"]
    assert rec["outcome"] == "unavailable" and "T2" in rec["reason"], rec


def test_a_tree_bound_to_a_released_item_is_where_the_next_item_is_worked(repo):
    """The case the feature exists for: the harness tree is still bound to an earlier
    item that was merged and let go of its lease; newer commits there are the
    --no-worktree item's, and its gates run there -- the tree is nobody else's live work."""
    tree = repo.parent / "agent-tree"
    run_cli(repo, "init")
    run_cli(repo, "config", "--set", "gate.unit_tests.command", PROBE)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ddflow")
    _git(repo, "worktree", "add", "-q", str(tree), "-b", "agent-work")
    run_cli(repo, "task", "add", "T0", "--title", "earlier", "--globs", "z.py")
    run_cli(repo, "task", "add", "T1", "--title", "add a", "--globs", "a.py")
    assert run_cli(tree, "claim", "T0")[0] == OK
    (tree / "z.py").write_text("z = 1\n")
    _git(tree, "add", "z.py")
    _git(tree, "commit", "-qm", "T0")
    assert run_cli(tree, "merge", "T0")[0] == OK
    assert run_cli(tree, "release", "T0")[0] == OK
    assert run_cli(tree, "claim", "T1", "--no-worktree")[0] == OK
    (tree / "a.py").write_text("a = 1\n")
    _git(tree, "add", "a.py")
    _git(tree, "commit", "-qm", "T1")
    code, out, err = run_cli(tree, "--json", "gate", "run", "T1", "unit_tests")
    assert code == OK, out + err
    assert json.loads(out)["outcome"] == "passed"
