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
    assert "ddflow:begin commands/implement" in (tree / ".claude/commands/implement.md").read_text()
    assert (repo / ".claude/commands/implement.md").read_text() == "the project's own\n"


def test_init_in_a_linked_worktree_writes_there_too(repo):
    """`init` writes tracked files as well (.ddflow/config.toml, .gitignore,
    .gitattributes): the same defect, found by the review of this fix."""
    tree = _worktree(repo)
    p = _ddflow(tree, "init")
    assert p.returncode == 0, p.stderr
    status = _git(repo, "status", "--porcelain", "--untracked-files=all").splitlines()
    shared = (".ddflow/events/", ".ddflow/index.db", ".ddflow/local/")
    assert [line for line in status if not line[3:].startswith(shared)] == []
    for rel in (".ddflow/config.toml", ".ddflow/.gitignore", ".gitignore", ".gitattributes"):
        assert (tree / rel).is_file(), f"{rel} belongs in the caller's tree"


def test_files_tree_falls_back_to_the_repo_only_outside_it(repo, tmp_path):
    """The fallbacks: no caller, a non-git directory, another repository -> `repo`;
    a detached linked worktree of this repository is still the caller's tree."""
    from ddflow.api.setup import files_tree

    tree = _worktree(repo)
    assert files_tree(repo, None) == repo
    plain = tmp_path / "plain"
    plain.mkdir()
    assert files_tree(repo, plain) == repo
    other = tmp_path / "other"
    other.mkdir()
    _git(other, "init", "-q")
    assert files_tree(repo, other) == repo
    _git(tree, "checkout", "-q", "--detach")
    assert files_tree(repo, tree) == tree.resolve()


def test_mcp_setup_from_a_server_in_a_linked_worktree_writes_there(repo):
    """Bug B1e7ad10c6c: the same over MCP. A server standing in a linked worktree
    (`called_from`) ran `ddflow_setup` against the primary, writing every file there."""
    from ddflow.surfaces.mcp import Server

    tree = _worktree(repo)
    reply = Server(repo, called_from=tree).handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "ddflow_setup", "arguments": {"agents": "claude"}},
        }
    )
    assert reply is not None and not reply["result"].get("isError"), reply
    status = _git(repo, "status", "--porcelain", "--untracked-files=all").splitlines()
    shared = (".ddflow/events/", ".ddflow/index.db", ".ddflow/local/")
    assert [line for line in status if not line[3:].startswith(shared)] == []
    for rel in ("AGENTS.md", ".mcp.json", ".ddflow/config.toml"):
        assert (tree / rel).is_file(), f"{rel} belongs in the server's tree"


def test_mcp_setup_under_a_foreign_as_agent_does_not_write_into_the_parents_tree(repo):
    """A subagent sharing the connection (its own `as_agent`) is not standing in the
    parent's harness tree: its setup must not land on the parent's branch. It is
    answered as from the primary, as every `wants_called_from` tool is (B11e4c5a185)."""
    from ddflow.surfaces.mcp import Server

    tree = _worktree(repo)
    reply = Server(repo, agent="parent", called_from=tree).handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "ddflow_setup",
                "arguments": {"agents": "claude", "as_agent": "sub"},
            },
        }
    )
    assert reply is not None and not reply["result"].get("isError"), reply
    assert not (tree / "AGENTS.md").exists(), "the parent's branch got the subagent's files"
