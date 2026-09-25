"""When the agent is already in a worktree, bind the item to THAT one.

`claim` used to create a worktree unconditionally. An agent whose harness had already
isolated it — Claude Code and Cursor both do — was sent to a second tree on a second
branch, stranding the uncommitted work in the first and giving one item two branches:

    $ git worktree add ../agent-tree -b agent-work     # the harness
    $ ddflow --repo ../agent-tree claim T1
      worktree: /tmp/.ddflow-worktrees/T1
      branch:   ddflow/T1 (from main)
      cd there and work.                                # ...away from the work

ddflow never needed to have CREATED the tree. It needs to know WHICH tree an item is
worked in, so `recover` can find stranded work and `merge` knows what to merge. Being
told is as good as having made it — and `worktree.created` stays False for an adopted
tree, so `remove_on_merge` will not delete something ddflow did not make.

*Operator challenge, 2026-09-25: "wouldn't the agent manage worktrees and we only
orchestrate them?" Largely yes, for creation. Not for merge — see the class docstring
on `ddflow merge`, which merges into the primary without a checkout there.*
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.core.model import fold
from ddflow.infra.log import EventLog

OK, FAIL, NOTHING, REFUSED = 0, 1, 2, 3


def _git(where, *args) -> None:
    subprocess.run(["git", "-C", str(where), *args], check=True, capture_output=True)


def _agent_worktree(repo: Path, name: str = "agent-tree", branch: str = "agent-work") -> Path:
    """What Claude Code / Cursor do before ddflow is ever called."""
    path = repo.parent / name
    _git(repo, "worktree", "add", "-q", str(path), "-b", branch)
    return path


def _item(repo: Path, iid: str = "T1"):
    return fold(EventLog(repo).read_all(), strict=False).items[iid]


def _worktrees(repo: Path) -> list[str]:
    out = subprocess.run(
        ["git", "-C", str(repo), "worktree", "list", "--porcelain"],
        capture_output=True,
        text=True,
    ).stdout
    return [ln.split(" ", 1)[1] for ln in out.splitlines() if ln.startswith("worktree ")]


# -- adoption ---------------------------------------------------------------------------


def test_claiming_from_inside_a_worktree_adopts_it(repo):
    """The headline. No third tree, no second branch, no 'cd elsewhere'."""
    run_cli(repo, "init")
    agent_tree = _agent_worktree(repo)
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")

    before = len(_worktrees(repo))
    code, out, err = run_cli(agent_tree, "claim", "T1")
    assert code == OK, err
    assert len(_worktrees(repo)) == before, f"a rival worktree was created:\n{out}"
    assert "adopted" in out and "Carry on where you are" in out, out


def test_the_adopted_tree_and_branch_are_recorded(repo):
    """Recording is the part ddflow actually needs: `recover` finds stranded work by
    the tree, and `merge` needs the branch. Adoption without a record would trade one
    bug for a worse one."""
    run_cli(repo, "init")
    agent_tree = _agent_worktree(repo)
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")
    run_cli(agent_tree, "claim", "T1")

    it = _item(repo)
    assert it.branch == "agent-work", it.branch
    assert it.worktree, "no worktree path recorded for the adopted tree"
    assert "agent-tree" in it.worktree, it.worktree


def test_an_adopted_tree_is_not_marked_as_created(repo):
    """`remove_on_merge` deletes trees ddflow made. Deleting the agent's own tree —
    which its harness created and may still be using — would be a far worse failure
    than the one adoption fixes."""
    run_cli(repo, "init")
    agent_tree = _agent_worktree(repo)
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")
    run_cli(agent_tree, "claim", "T1")

    kinds = [e.kind for e in EventLog(repo).read_all() if e.kind.startswith("worktree.")]
    assert "worktree.adopted" in kinds, kinds
    assert "worktree.created" not in kinds, kinds


def test_claiming_from_the_PRIMARY_still_creates_one(repo):
    """The ordinary single-tree case must not change shape. Adoption fires only when
    the caller is demonstrably already isolated."""
    run_cli(repo, "init")
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")
    code, out, err = run_cli(repo, "claim", "T1")
    assert code == OK, err
    assert "cd there and work" in out, out
    kinds = [e.kind for e in EventLog(repo).read_all() if e.kind.startswith("worktree.")]
    assert "worktree.created" in kinds


# -- the guard --------------------------------------------------------------------------


def test_a_tree_already_bound_to_an_open_item_is_refused(repo):
    """Two items in one tree cannot be merged or recovered separately: `merge` would
    take one item's branch for the other's work, and `recover` could not say whose
    uncommitted changes it found."""
    run_cli(repo, "init")
    agent_tree = _agent_worktree(repo)
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")
    run_cli(repo, "task", "add", "T2", "--globs", "b.py")
    run_cli(agent_tree, "claim", "T1")

    code, _out, err = run_cli(agent_tree, "claim", "T2")
    assert code == REFUSED, f"two items were bound to one tree: {code}"
    assert "T1" in err, err
    assert "--no-worktree" in err, "refused without naming a way forward"


def test_a_tree_whose_item_is_DONE_can_be_reused(repo):
    """Reusing the tree of finished work is exactly what an agent should be able to do.
    Refusing forever would make the guard a leak."""
    run_cli(repo, "init")
    agent_tree = _agent_worktree(repo)
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")
    run_cli(repo, "task", "add", "T2", "--globs", "b.py")
    run_cli(agent_tree, "claim", "T1")
    run_cli(agent_tree, "abandon", "T1", "--reason", "not needed")

    code, _out, err = run_cli(agent_tree, "claim", "T2")
    assert code == OK, f"a settled item's tree could not be reused: {err}"


# -- the escape hatches ------------------------------------------------------------------


def test_no_worktree_still_skips_everything(repo):
    run_cli(repo, "init")
    agent_tree = _agent_worktree(repo)
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")
    code, out, err = run_cli(agent_tree, "claim", "T1", "--no-worktree")
    assert code == OK, err
    assert "adopted" not in out
    assert not _item(repo).worktree


def test_adopt_existing_false_restores_the_old_behaviour(repo):
    """The knob is the way back for anyone who wants ddflow's own tree regardless."""
    run_cli(repo, "init")
    agent_tree = _agent_worktree(repo)
    # Through the supported path, which also proves the knob is reachable the way an
    # operator would actually set it. Appending a second `[worktree]` table by hand is
    # invalid TOML — `init` already wrote one.
    assert run_cli(repo, "config", "--set", "worktree.adopt_existing", "false")[0] == OK
    run_cli(repo, "task", "add", "T1", "--globs", "a.py")
    code, out, err = run_cli(agent_tree, "claim", "T1")
    assert code == OK, err
    assert "adopted" not in out, out
    assert "cd there and work" in out


def test_a_detached_head_is_not_adopted(repo):
    """There is a tree but no branch to merge from later. Reported as nothing to adopt
    rather than adopted with an empty branch, so the failure lands at claim time rather
    than at merge time, after the work is done."""
    from ddflow.infra.worktree import current

    run_cli(repo, "init")
    agent_tree = _agent_worktree(repo)
    sha = subprocess.run(
        ["git", "-C", str(agent_tree), "rev-parse", "HEAD"], capture_output=True, text=True
    ).stdout.strip()
    _git(agent_tree, "checkout", "-q", "--detach", sha)
    assert current(agent_tree) is None


def test_the_detector_says_no_in_the_primary(repo):
    from ddflow.infra.worktree import current

    assert current(repo) is None
