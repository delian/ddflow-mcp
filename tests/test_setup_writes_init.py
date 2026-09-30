"""`ddflow_setup` over MCP leaves a project in the same state `ddflow adopt` does.

Bug B185ec008b4: the four init writes -- the starter `.ddflow/config.toml`,
`.ddflow/.gitignore`, the root `.gitignore` entry for `.ddflow-worktrees/` and the
`.gitattributes` `merge=union` line for the event log -- lived in the CLI's `cmd_init`,
which only `ddflow adopt` and `ddflow init` call. An agent that onboarded a project over
MCP got the drivers and the AGENTS.md block and none of those: the derived index was
committable, every worktree showed up as untracked files, and two clones' event logs
conflicted on merge instead of concatenating.

The comparison is between the two SURFACES on two fresh repositories, byte for byte,
because the bug class is divergence: a test that only checked the MCP path for "a
.gitignore exists" would pass again the next time one surface grew a line the other
did not.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.surfaces.mcp import Server

#: The files the init step owns or edits. Each one is checked on both surfaces.
INIT_FILES = (".ddflow/config.toml", ".ddflow/.gitignore", ".gitignore", ".gitattributes")


def _fresh(root: Path) -> Path:
    root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    for k, v in (("user.email", "t@example.com"), ("user.name", "Test")):
        subprocess.run(["git", "-C", str(root), "config", k, v], check=True)
    return root


def _mcp_setup(repo: Path) -> dict:
    reply = Server(repo).handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "ddflow_setup", "arguments": {"agents": "claude"}},
        }
    )
    assert reply is not None
    return reply["result"]


def test_mcp_setup_writes_the_same_init_files_as_cli_adopt(tmp_path):
    via_cli = _fresh(tmp_path / "cli")
    via_mcp = _fresh(tmp_path / "mcp")

    code, _out, err = run_cli(via_cli, "adopt", "--agents", "claude")
    assert code == 0, err
    result = _mcp_setup(via_mcp)
    assert not result.get("isError"), result

    for rel in INIT_FILES:
        assert (via_cli / rel).is_file(), f"CLI adopt did not write {rel}"
        assert (via_mcp / rel).is_file(), f"ddflow_setup over MCP did not write {rel}"
        assert (via_mcp / rel).read_text("utf-8") == (via_cli / rel).read_text("utf-8"), (
            f"{rel} differs between the two surfaces"
        )


def test_mcp_setup_leaves_the_log_committable_and_the_index_ignored(tmp_path):
    """What the files are FOR, asked of git itself: the index stays out of commits, the
    event log goes in, and worktrees under the repo are not untracked noise."""
    repo = _fresh(tmp_path / "p")
    _mcp_setup(repo)

    def ignored(rel: str) -> bool:
        return subprocess.run(["git", "-C", str(repo), "check-ignore", "-q", rel]).returncode == 0

    assert ignored(".ddflow/index.db")
    assert ignored(".ddflow-worktrees/x")
    assert not ignored(".ddflow/events/a.jsonl")
    attr = subprocess.run(
        ["git", "-C", str(repo), "check-attr", "merge", ".ddflow/events/a.jsonl"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert attr.strip().endswith("merge: union"), attr


@pytest.mark.parametrize("surface", ["cli", "mcp"])
def test_setup_keeps_the_projects_own_files(tmp_path, surface):
    """Adopting into an existing project APPENDS to its .gitignore and .gitattributes
    and never replaces a config.toml it already has -- on either surface."""
    repo = _fresh(tmp_path / "p")
    (repo / ".gitignore").write_text("node_modules/", "utf-8")  # no trailing newline
    (repo / ".gitattributes").write_text("*.png binary\n", "utf-8")
    (repo / ".ddflow").mkdir()
    (repo / ".ddflow" / "config.toml").write_text("[lease]\nttl_s = 60\n", "utf-8")

    if surface == "cli":
        code, _out, err = run_cli(repo, "adopt", "--agents", "claude")
        assert code == 0, err
    else:
        assert not _mcp_setup(repo).get("isError")

    assert (repo / ".ddflow" / "config.toml").read_text("utf-8") == "[lease]\nttl_s = 60\n"
    gi = (repo / ".gitignore").read_text("utf-8")
    assert gi.startswith("node_modules/\n") and ".ddflow-worktrees/" in gi
    ga = (repo / ".gitattributes").read_text("utf-8")
    assert ga.startswith("*.png binary\n") and ".ddflow/events/*.jsonl merge=union" in ga

    # Idempotent: a second run SUCCEEDS and appends nothing. The exit is checked, or a
    # second run that failed before writing anything would pass as idempotent.
    if surface == "cli":
        code, _out, err = run_cli(repo, "adopt", "--agents", "claude")
        assert code == 0, err
    else:
        assert not _mcp_setup(repo).get("isError")
    assert (repo / ".gitignore").read_text("utf-8") == gi
    assert (repo / ".gitattributes").read_text("utf-8") == ga


def test_a_neighbouring_rule_does_not_count_as_ours(tmp_path):
    """Bug B63d0028716: "already present" was a SUBSTRING test. A project whose
    .gitattributes already said `.ddflow/events/*.jsonl -diff` never got the union
    merge, and `.ddflow-worktrees-old/` in .gitignore stood in for `.ddflow-worktrees/`.
    Asked of git, because what matters is what git does with the result."""
    from ddflow.services.adopt import init_files

    repo = _fresh(tmp_path / "p")
    (repo / ".gitignore").write_text(".ddflow-worktrees-old/\n", "utf-8")
    (repo / ".gitattributes").write_text(".ddflow/events/*.jsonl -diff\n", "utf-8")

    init_files(repo)

    assert (
        subprocess.run(
            ["git", "-C", str(repo), "check-ignore", "-q", ".ddflow-worktrees/x"]
        ).returncode
        == 0
    )
    attr = subprocess.run(
        ["git", "-C", str(repo), "check-attr", "merge", ".ddflow/events/a.jsonl"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert attr.strip().endswith("merge: union"), attr
