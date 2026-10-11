"""A fresh clone has no git hooks and tracked configs of another machine; doctor says so (Bec9a971c5d).

Git does not track hooks, so a clone of an adopted project starts with none, and `hooks
status` (exit 2) was the only thing that said the pre-commit and commit-msg checks were
not running: `doctor` read Healthy. And `.mcp.json` / `.claude/settings.json` are
committed with the adopter's absolute interpreter and PYTHONPATH, so they differ per
machine and dangle on every other one.

`doctor` now prints a NOTE for each -- a PROBLEM for the missing hooks only when
`[enforce].commit_without_lease = "block"`, because every CI checkout is a fresh clone.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from helpers import git_raw as _git

from tests.conftest import run_cli


def _commit_all(repo: Path, msg: str) -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", msg, "--no-verify")


def _clone(src: Path, dest: Path) -> Path:
    subprocess.run(["git", "clone", "-q", str(src), str(dest)], check=True)
    for k, v in (("user.email", "t@example.com"), ("user.name", "T"), ("commit.gpgsign", "false")):
        _git(dest, "config", k, v)
    return dest


@pytest.fixture
def adopted(repo: Path) -> Path:
    """An adopted project whose files are all committed: what a teammate clones."""
    assert run_cli(repo, "adopt", "--agents", "claude", "--launch", "python")[0] == 0
    assert run_cli(repo, "hooks", "install")[0] == 0
    _commit_all(repo, "adopt")
    return repo


def _doctor(repo: Path) -> tuple[int, str]:
    code, out, err = run_cli(repo, "doctor")
    return code, out + err


def _hook_lines(text: str) -> list[str]:
    return [ln for ln in text.splitlines() if "git hook" in ln and "ddflow hooks install" in ln]


def _local_lines(text: str) -> list[str]:
    return [ln for ln in text.splitlines() if "(tracked)" in ln and "machine" in ln]


def test_a_clone_without_the_managed_hooks_gets_a_note_that_names_them(adopted, tmp_path):
    clone = _clone(adopted, tmp_path / "clone")
    code, text = _doctor(clone)
    lines = _hook_lines(text)
    assert len(lines) == 1, text
    assert "pre-commit" in lines[0] and "commit-msg" in lines[0], lines
    assert code == 0, text  # a NOTE: a CI checkout is always fresh


def test_the_missing_hooks_are_a_problem_under_a_block_policy(adopted, tmp_path):
    cfg = adopted / ".ddflow" / "config.toml"
    cfg.write_text(
        cfg.read_text().replace('commit_without_lease = "warn"', 'commit_without_lease = "block"')
    )
    assert 'commit_without_lease = "block"' in cfg.read_text()
    _commit_all(adopted, "block")
    clone = _clone(adopted, tmp_path / "clone")
    code, text = _doctor(clone)
    assert _hook_lines(text), text
    assert code == 1, text


def test_installed_hooks_produce_no_line(adopted):
    assert _hook_lines(_doctor(adopted)[1]) == []


def test_an_unadopted_project_gets_no_hook_line(repo):
    assert run_cli(repo, "init")[0] == 0
    assert _hook_lines(_doctor(repo)[1]) == []


def test_tracked_configs_with_this_machines_launcher_get_a_note(adopted, tmp_path):
    assert "PYTHONPATH" in (adopted / ".mcp.json").read_text()  # the premise
    clone = _clone(adopted, tmp_path / "clone")
    _, text = _doctor(clone)
    lines = _local_lines(text)
    assert len(lines) == 1, text
    assert ".mcp.json" in lines[0] and ".claude/settings.json" in lines[0], lines
    assert "ddflow adopt --launch uvx" in lines[0] and "git-ignore" in lines[0], lines
    assert "dangling" in lines[0], lines


def test_a_path_relative_entry_produces_no_machine_line(adopted):
    (adopted / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"ddflow": {"command": "ddflow", "args": []}}})
    )
    (adopted / ".claude" / "settings.json").write_text("{}\n")
    _commit_all(adopted, "portable")
    assert _local_lines(_doctor(adopted)[1]) == []


def test_untracked_configs_are_not_reported(adopted):
    _git(adopted, "rm", "-q", "--cached", ".mcp.json", ".claude/settings.json")
    _git(adopted, "commit", "-qm", "untrack", "--no-verify")
    assert _local_lines(_doctor(adopted)[1]) == []
