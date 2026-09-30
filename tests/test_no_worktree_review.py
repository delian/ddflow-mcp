"""`review` of an item never diffs the primary checkout's working tree (B60de9a57ed).

An item claimed `--no-worktree` has no tree, and `diff_for` fell back to the PRIMARY's
working tree -- in a ddflow checkout, other agents' uncommitted event-log appends. The
reviewer read 43 KB of somebody else's JSON lines, found nothing wrong with them, and
the gate was recorded PASSED for a change it never saw. The same fallback caught an item
whose own tree had nothing to diff.

A review is of the item's work: a named branch, its tree, the branch checked out where
the caller stands, or -- for a lone agent working in the primary -- the primary's working
tree without ddflow's own bookkeeping. With nothing left it is recorded unavailable.
"""

from __future__ import annotations

import stat
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.core.model import fold
from ddflow.infra.log import EventLog

OK, FAIL, NOTHING, REFUSED = 0, 1, 2, 3
FOREIGN = "OTHER AGENT'S UNCOMMITTED EVENT"


def _git(where: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(where), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _setup(repo: Path, tmp_path: Path) -> tuple[Path, Path]:
    """(the harness tree the item is worked in, the file the reviewer saves its prompt to)."""
    seen = tmp_path / "seen.txt"
    cli = tmp_path / "fake-reviewer"
    cli.write_text(f"#!/bin/sh\ncat >> '{seen}'\nprintf 'STATUS: NO FINDINGS\\n'\n")
    cli.chmod(cli.stat().st_mode | stat.S_IXUSR)
    run_cli(repo, "init")
    (repo / ".ddflow" / "config.toml").write_text(
        f'[[reviewer]]\nname = "fake"\nkind = "command"\ncommand = "{cli}"\n'
        'model = "gemini-2.5-pro"\ngates = ["critic"]\n'
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ddflow")
    tree = repo.parent / "agent-tree"
    _git(repo, "worktree", "add", "-q", str(tree), "-b", "agent-work")
    run_cli(repo, "task", "add", "T1", "--title", "add a", "--globs", "a.py")
    (tree / "a.py").write_text("THE ITEM'S OWN CHANGE = 1\n")
    _git(tree, "add", "a.py")
    _git(tree, "commit", "-qm", "add a")
    # What a busy ddflow primary has: other agents' uncommitted event logs -- the
    # incident -- and, as the critic review pointed out, possibly any other uncommitted
    # edit, which a path filter on ddflow's own files would not keep out.
    other = repo / ".ddflow" / "events" / "other-agent.jsonl"
    other.write_text("{}\n")
    _git(repo, "add", "-f", str(other))
    _git(repo, "commit", "-qm", "another agent's log")
    other.write_text(f'{{}}\n{{"note": "{FOREIGN}"}}\n')
    (repo / "README.md").write_text(f"# proj\n{FOREIGN}\n")
    return tree, seen


def _critic(repo: Path) -> str:
    return fold(EventLog(repo).read_all(), strict=False).items["T1"].gates["critic"].outcome


def test_other_agents_event_logs_are_never_the_items_diff(repo, tmp_path):
    tree, seen = _setup(repo, tmp_path)
    run_cli(tree, "claim", "T1", "--no-worktree")
    code, out, err = run_cli(repo, "review", "T1")
    assert code == NOTHING, out + err
    assert _critic(repo) == "unavailable"
    assert not seen.exists() or FOREIGN not in seen.read_text()
    assert "--branch" in out + err


def test_it_reviews_the_branch_checked_out_where_the_caller_stands(repo, tmp_path):
    tree, seen = _setup(repo, tmp_path)
    run_cli(tree, "claim", "T1", "--no-worktree")
    code, out, err = run_cli(tree, "review", "T1")
    assert code == OK, out + err
    assert "THE ITEM'S OWN CHANGE" in seen.read_text()
    assert FOREIGN not in seen.read_text()
    assert _critic(repo) == "passed"


def test_a_named_branch_is_reviewed_from_anywhere(repo, tmp_path):
    tree, seen = _setup(repo, tmp_path)
    run_cli(tree, "claim", "T1", "--no-worktree")
    code, out, err = run_cli(repo, "review", "T1", "--branch", "agent-work")
    assert code == OK, out + err
    assert "THE ITEM'S OWN CHANGE" in seen.read_text()
    assert FOREIGN not in seen.read_text()


def test_an_item_whose_own_tree_has_nothing_to_diff_is_not_reviewed_against_the_primary(
    repo, tmp_path
):
    _tree, seen = _setup(repo, tmp_path)
    code, out, err = run_cli(repo, "claim", "T1")  # a fresh ddflow tree: no changes yet
    assert code == OK, out + err
    code, out, err = run_cli(repo, "review", "T1")
    assert code == NOTHING, out + err
    assert _critic(repo) == "unavailable"
    assert not seen.exists() or FOREIGN not in seen.read_text()


def test_an_unclaimed_item_still_reviews_the_primary_without_ddflows_bookkeeping(repo, tmp_path):
    """Unchanged for an item nobody claimed: the primary's working tree -- minus
    ddflow's own files, so other agents' event logs are never anyone's diff."""
    _tree, seen = _setup(repo, tmp_path)
    code, out, err = run_cli(repo, "review", "T1")
    assert code == OK, out + err
    text = seen.read_text()
    assert "# proj" in text  # the README edit is there ...
    assert (
        '"note"' not in text
    )  # ... the event log is not assert "# proj" in text  # the README edit is there ...
    assert '"note"' not in text  # ... the event log is not


def _another_items_tree(repo: Path) -> Path:
    """A second harness tree, adopted by another open item, T2."""
    other = repo.parent / "t2-tree"
    _git(repo, "worktree", "add", "-q", str(other), "-b", "t2-work")
    run_cli(repo, "task", "add", "T2", "--title", "t2", "--globs", "z.py")
    code, out, err = run_cli(other, "claim", "T2")
    assert code == OK and "adopted" in out, out + err
    return other


def test_another_items_tree_is_not_reviewed_as_this_items_work(repo, tmp_path):
    tree, seen = _setup(repo, tmp_path)
    run_cli(tree, "claim", "T1", "--no-worktree")
    other = _another_items_tree(repo)
    (other / "z.py").write_text("T2 WORK = 1\n")
    _git(other, "add", "z.py")
    _git(other, "commit", "-qm", "t2")
    code, out, err = run_cli(other, "review", "T1")
    assert code == NOTHING, out + err
    assert not seen.exists() or "T2 WORK" not in seen.read_text()
    assert "T2" in out + err
