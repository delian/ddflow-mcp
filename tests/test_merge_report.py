"""What `merge` reports, and what it leaves standing.

B9f8019c521: `merge` reported the merged BRANCH's head as its `sha` (4dd7b3e for
B-local-config-surfaces) instead of the commit it made on the base (cb36953). A caller
recording or citing the merge cited a commit that is not the landing -- and agents then
passed it on to `complete --sha`. Under a squash strategy that commit is not on the base
at all. The PR path already recorded the forge's merge commit; the local path now agrees.

B1172da8c35: `ddflow merge` run from inside the item's own worktree landed the work and
then deleted that worktree -- the caller's working directory. ddflow itself exited 0, but
the caller's shell did not survive it: a harness that runs `pwd` after each command
(Claude Code's does) got 'getcwd: cannot access parent directories' and reported a
landed merge as exit 1. The tree a caller stands in is now kept, with where to go.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import pass_pipeline, run_cli

from ddflow.core.model import fold
from ddflow.infra.log import EventLog

OK = 0
ROOT = Path(__file__).resolve().parents[1]


def _git(where: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(where), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _claimed_with_a_commit(repo: Path) -> Path:
    """T1 claimed into its own ddflow worktree, one commit made there."""
    run_cli(repo, "init")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ddflow")
    run_cli(repo, "task", "add", "T1", "--title", "add a", "--globs", "a.py")
    code, out, err = run_cli(repo, "--json", "claim", "T1")
    assert code == OK, out + err
    tree = Path(json.loads(out)["worktree"])
    (tree / "a.py").write_text("x = 1\n")
    _git(tree, "add", "a.py")
    _git(tree, "commit", "-qm", "add a")
    return tree


def _merged_events(repo: Path) -> list[dict]:
    return [e.data for e in EventLog(repo).read_all() if e.kind == "worktree.merged"]


def test_merge_reports_the_commit_it_made_on_the_base_not_the_branch_head(repo):
    tree = _claimed_with_a_commit(repo)
    head = _git(tree, "rev-parse", "HEAD")
    code, out, err = run_cli(repo, "--json", "merge", "T1")
    assert code == OK, out + err
    main = _git(repo, "rev-parse", "main")
    assert main != head, "the default strategy makes a merge commit"
    body = json.loads(out)
    assert body["sha"] == main
    assert body["branch_head"] == head
    # The log says the same, so `show`, versions and ports cite the landing too.
    (ev,) = _merged_events(repo)
    assert ev["sha"] == main and ev["branch_head"] == head
    assert fold(EventLog(repo).read_all(), strict=False).items["T1"].merged_sha == main


def test_the_human_line_names_the_merge_commit(repo):
    _claimed_with_a_commit(repo)
    code, out, err = run_cli(repo, "merge", "T1")
    assert code == OK, out + err
    assert f"({_git(repo, 'rev-parse', 'main')[:8]})" in out


def test_complete_without_sha_reports_the_merge_commit(repo):
    _claimed_with_a_commit(repo)
    assert run_cli(repo, "merge", "T1")[0] == OK
    pass_pipeline(repo, "T1")
    code, out, err = run_cli(repo, "--json", "complete", "T1", "--model", "claude-opus-5")
    assert code == OK, out + err
    # `complete` then commits the event log on top (Bcd3512c891): the landing is below it.
    assert json.loads(out)["sha"] == _git(repo, "rev-parse", "main^")
    assert _git(repo, "log", "-1", "--format=%s", "main").startswith("events: complete T1")


def _ddflow_in(tree: Path, script: str) -> subprocess.CompletedProcess:
    """A shell standing in ``tree`` runs ``script`` -- as an agent's harness does."""
    env = {**os.environ, "PYTHONPATH": str(ROOT), "DDFLOW_AGENT": "agent-test"}
    ddflow = f"{sys.executable} -m ddflow"
    return subprocess.run(
        ["bash", "-c", script.replace("ddflow", ddflow)],
        cwd=tree,
        capture_output=True,
        text=True,
        env=env,
        timeout=300,
    )


def test_merge_from_inside_the_worktree_leaves_the_callers_shell_standing(repo):
    tree = _claimed_with_a_commit(repo)
    # `pwd -P` after the command is exactly what the harness ran, and what failed.
    p = _ddflow_in(tree, "ddflow merge T1 && pwd -P")
    assert p.returncode == OK, p.stdout + p.stderr
    assert "getcwd" not in p.stderr
    assert _git(repo, "show", "main:a.py") == "x = 1"
    assert tree.is_dir()
    # And it says why, and where to go.
    assert "standing in it" in p.stderr
    assert f"cd {repo}" in p.stderr


def test_merge_from_elsewhere_still_removes_the_tree(repo):
    tree = _claimed_with_a_commit(repo)
    code, out, err = run_cli(repo, "--json", "merge", "T1")
    assert code == OK, out + err
    assert not tree.exists()
    assert json.loads(out)["worktree_removed"] is True
