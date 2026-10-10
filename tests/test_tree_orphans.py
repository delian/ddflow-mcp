"""One tree-side orphan detector for `cleanup` and `doctor` (B-uni-tree-lifecycle.4-orphans).

`doctor` carried its own test for "a worktree no item claims": a SUBSTRING match of the
branch prefix against the branch name, and a comparison of un-normalised paths. A branch
that merely contained the prefix (`someone/ddflow-experiments`) was reported as ours, and
a tree an item claimed under a differently-spelled path was reported as unclaimed.
`cleanup.unclaimed_trees` is now the one answer, built from the same helpers as `survey`.

Real git repositories: the property under test is what git lists.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from helpers import git_quiet as _git

from ddflow import api
from ddflow.config import Config
from ddflow.core.model import Item, State, fold
from ddflow.infra.log import EventLog
from ddflow.services import cleanup as CL

OK = 0


def _notes(repo: Path) -> list[str]:
    return list(api.doctor(repo).data["notes"])


def _unclaimed(repo: Path) -> list[Path]:
    state = fold(EventLog(repo).read_all(), strict=False)
    return [Path(p).resolve() for p in CL.unclaimed_trees(repo, Config.load(repo), state)]


def test_a_foreign_branch_that_only_contains_the_prefix_is_not_ours(repo):
    run_cli(repo, "init")
    foreign = repo.parent / "foreign-tree"
    _git(repo, "worktree", "add", "-q", str(foreign), "-b", "someone/ddflow-experiments")

    assert foreign.resolve() not in _unclaimed(repo)
    assert not [n for n in _notes(repo) if str(foreign.resolve()) in n and "no item claims" in n]


def test_a_tree_on_our_branch_that_no_item_claims_is_reported_once(repo):
    run_cli(repo, "init")
    prefix = Config.load(repo).worktree.branch_prefix
    stray = repo.parent / "stray-tree"
    _git(repo, "worktree", "add", "-q", str(stray), "-b", f"{prefix}stray")

    assert _unclaimed(repo) == [stray.resolve()]
    hits = [n for n in _notes(repo) if "no item claims" in n]
    assert len(hits) == 1 and str(stray.resolve()) in hits[0], hits


def test_a_claimed_tree_is_not_unclaimed_and_survey_agrees(repo):
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")
    code, _, err = run_cli(repo, "claim", "T1", agent="worker")
    assert code == OK, err

    assert _unclaimed(repo) == []
    assert not [n for n in _notes(repo) if "no item claims" in n]
    state = fold(EventLog(repo).read_all(), strict=False)
    plan = CL.survey(repo, Config.load(repo), state)
    assert [t.item for t in plan.trees] == ["T1"]


def test_doctor_and_survey_name_the_same_unclaimed_trees(repo):
    run_cli(repo, "init")
    prefix = Config.load(repo).worktree.branch_prefix
    for n in ("one", "two"):
        _git(repo, "worktree", "add", "-q", str(repo.parent / n), "-b", f"{prefix}{n}")
    state = fold(EventLog(repo).read_all(), strict=False)
    plan = CL.survey(repo, Config.load(repo), state)

    surveyed = sorted(Path(t.path).resolve() for t in plan.trees if not t.item)
    assert surveyed == sorted(_unclaimed(repo))
    assert len(surveyed) == 2
    said = sorted(
        Path(n.split()[1]).resolve() for n in _notes(repo) if n.endswith("no item claims it")
    )
    assert said == surveyed


def test_a_tree_claimed_under_a_differently_spelled_path_is_claimed(repo):
    run_cli(repo, "init")
    prefix = Config.load(repo).worktree.branch_prefix
    tree = repo.parent / "spelled-tree"
    _git(repo, "worktree", "add", "-q", str(tree), "-b", f"{prefix}spelled")
    cfg = Config.load(repo)
    # Same directory, spelled with a `..` detour; the item's branch does not match either.
    spelled = f"{repo}/../{tree.name}"
    state = State(items={"T1": Item(id="T1", kind="task", worktree=spelled, branch="other")})

    assert CL.unclaimed_trees(repo, cfg, state) == []
    assert [Path(p).resolve() for p in CL.unclaimed_trees(repo, cfg, State())] == [tree.resolve()]
