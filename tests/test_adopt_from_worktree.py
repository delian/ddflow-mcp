"""Bug Bfeb62112d9: `adopt` run from inside a linked worktree wrote the project's
TRACKED files into the PRIMARY checkout.

The repository root ddflow works against is the primary (`repo_root` resolves through
`--git-common-dir`, so every agent shares one event log). That is right for the log and
wrong for the files `adopt` writes to be COMMITTED -- the driver docs, the rules blocks,
the MCP registrations, `.gitignore`/`.gitattributes`: on run_nemo_run they dirtied the
shared primary while the caller's own branch got nothing.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout


def _ddflow(cwd: Path, *argv: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    env.pop("DDFLOW_AGENT", None)
    return subprocess.run(
        [sys.executable, "-m", "ddflow", *argv],
        cwd=cwd,
        capture_output=True,
        text=True,
        env=env,
        timeout=300,
    )


def _worktree(repo: Path) -> Path:
    (repo / "README.md").write_text("x\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init")
    tree = repo.parent / "harness"
    _git(repo, "worktree", "add", "-q", "-b", "harness", str(tree))
    return tree


def test_adopt_in_a_linked_worktree_writes_there_and_leaves_the_primary_clean(repo):
    tree = _worktree(repo)
    p = _ddflow(tree, "adopt", "--agents", "claude", "--launch", "python")
    assert p.returncode == 0, p.stderr
    status = _git(repo, "status", "--porcelain", "--untracked-files=all").splitlines()
    # The shared runtime state IS the primary's: the event log, the index, local probes.
    shared = (".ddflow/events/", ".ddflow/index.db", ".ddflow/local/")
    written = [line for line in status if not line[3:].startswith(shared)]
    assert written == [], "the primary must not get the files adopt writes to be committed"
    for rel in (
        "AGENTS.md",
        "CLAUDE.md",
        ".mcp.json",
        ".gitattributes",
        ".ddflow/config.toml",
        "docs/ddflow/drivers/implement-phase.md",
        ".claude/commands/implement.md",
    ):
        assert (tree / rel).is_file(), f"{rel} belongs in the caller's tree"
    assert "git add" in p.stdout and str(tree) not in _git(repo, "status", "--porcelain")


def test_a_command_file_deleted_in_the_worktree_is_judged_there(repo):
    """The worktree, not the primary, decides whether the command file is the project's."""
    (repo / ".claude" / "commands").mkdir(parents=True)
    (repo / ".claude" / "commands" / "implement.md").write_text("the project's own\n")
    tree = _worktree(repo)
    (tree / ".claude" / "commands" / "implement.md").unlink()
    p = _ddflow(tree, "adopt", "--agents", "claude", "--launch", "python")
    assert p.returncode == 0, p.stderr
    assert "DDFLOW:MANAGED" in (tree / ".claude/commands/implement.md").read_text()
    assert (repo / ".claude/commands/implement.md").read_text() == "the project's own\n"
