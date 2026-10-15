"""B10725b2c5e: a generated view is checked against the log AS COMMITTED.

The check compared a staged rule file or view with the LIVE shards in the primary
checkout, so any agent appending an event while the commit was made refused it with
"the event log it is rendered from has N change(s) the commit does not record". A view
must agree with the log committed beside it, and nothing else: events another agent is
writing right now are not part of this commit.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from helpers import git_proc as _git

VIEW = "docs/ddflow/QUEUE.md"


def _commit(repo: Path, *paths: str) -> subprocess.CompletedProcess:
    _git(repo, "add", *paths)
    return subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "views"],
        capture_output=True,
        text=True,
        env={**os.environ},
        timeout=180,
    )


@pytest.fixture
def adopted(repo):
    run_cli(repo, "adopt", "--agents", "claude")
    (repo / ".ddflow" / "config.toml").write_text('[enforce]\ncommit_without_lease = "off"\n')
    run_cli(repo, "phase", "add", "P1", "--title", "Core")
    run_cli(repo, "task", "add", "P1.T1", "--phase", "P1", "--globs", "src/a.py")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "scaffold", "--no-verify")
    return repo


def _another_agent_writes(repo: Path, name: str = "T2") -> None:
    code, _o, err = run_cli(
        repo, "task", "add", f"P1.{name}", "--phase", "P1", "--globs", "src/z.py", agent="other"
    )
    assert code == 0, err


def test_a_view_commits_while_another_agent_appends_events(adopted):
    run_cli(adopted, "task", "add", "P1.T3", "--phase", "P1", "--globs", "src/c.py")
    assert run_cli(adopted, "render")[0] == 0
    _git(adopted, "add", VIEW, ".ddflow/events")
    _another_agent_writes(adopted)  # a new, untracked/modified shard nobody staged
    r = subprocess.run(
        ["git", "-C", str(adopted), "commit", "-m", "views"],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert r.returncode == 0, f"a concurrent writer refused the commit:\n{r.stderr}"
    assert "P1.T2" not in _git(adopted, "show", "HEAD:" + VIEW).stdout


def test_a_rule_file_commits_while_another_agent_appends_events(adopted):
    code, _o, err = run_cli(
        adopted, "rule", "add", "--id", "r-race", "--title", "Race", "--content", "x", "--new"
    )
    assert code == 0, err
    files = sorted(p.name for p in (adopted / ".ddflow" / "rules").glob("*.toml"))
    assert files, "rule add wrote no rule file"
    _git(adopted, "add", ".ddflow/rules", ".ddflow/events")
    _another_agent_writes(adopted)
    r = subprocess.run(
        ["git", "-C", str(adopted), "commit", "-m", "rule"],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert r.returncode == 0, f"a concurrent writer refused the rule file:\n{r.stderr}"


def test_a_view_whose_source_events_are_not_committed_is_still_refused(adopted):
    run_cli(adopted, "task", "add", "P1.T3", "--phase", "P1", "--globs", "src/c.py")
    assert run_cli(adopted, "render")[0] == 0
    _another_agent_writes(adopted)
    r = _commit(adopted, VIEW)  # the events the view was rendered from are NOT staged
    assert r.returncode != 0, "a view ahead of its committed log was committed"
    assert "does not record" in r.stderr, r.stderr
