"""`ddflow review --detach` re-declares the caller's RESOLVED identity (bug
B-review-identity-from-worktree-ignores-as-agent).

The detach relaunch rebuilds its argv from the typed `sys.argv` and strips only
`--detach`. An identity the caller holds over `as_agent`, `DDFLOW_AGENT` or a harness's
`ddflow_identify` declaration never rides in argv, so the detached process -- now standing
in a claimed worktree -- fell through `identity.resolve` to `default_agent_id` and recorded
the review under ``<host>-<item>-<hash>`` instead of the role the agent actually plays.
`_detach_argv` carries the resolved id through so shell, MCP and hooks attribute to one
holder.
"""

from __future__ import annotations

import argparse
import shlex
from pathlib import Path

import pytest

from ddflow.core import outcome as O
from ddflow.surfaces.commands import review as R
from ddflow.surfaces.context import Ctx


@pytest.fixture(autouse=True)
def _clean_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DDFLOW_AGENT", raising=False)
    monkeypatch.delenv("CLAUDECODE", raising=False)


@pytest.fixture
def adopted(repo: Path) -> Path:
    (repo / ".ddflow").mkdir(exist_ok=True)
    return repo


def _ctx(repo: Path, agent: str = "") -> Ctx:
    return Ctx(argparse.Namespace(repo=str(repo), agent=agent, json=False))


def test_detach_argv_redeclares_an_env_sourced_identity(adopted, monkeypatch):
    monkeypatch.setenv("DDFLOW_AGENT", "sz-dry-run")
    c = _ctx(adopted)
    assert c.log.agent_id == "sz-dry-run", "the identity DDFLOW_AGENT resolved"
    assert R._detach_argv(["review", "T1"], c.log.agent_id) == [
        "--agent",
        "sz-dry-run",
        "review",
        "T1",
    ]


def test_detach_argv_replaces_a_typed_agent(monkeypatch):
    """A `--agent` already typed still relaunches under the RESOLVED holder, not the typed
    token."""
    assert R._detach_argv(["--agent", "flagged", "review", "T1"], "sz-dry-run") == [
        "--agent",
        "sz-dry-run",
        "review",
        "T1",
    ]
    assert R._detach_argv(["--agent=flagged", "review", "T1"], "sz-dry-run") == [
        "--agent",
        "sz-dry-run",
        "review",
        "T1",
    ]


def test_detach_argv_keeps_a_positional_agent_past_the_separator(monkeypatch):
    """A `--agent=...` PAST the `--` end-of-options separator is a positional, not the
    global identity flag, so it stays."""
    argv = ["review", "T1", "--", "--agent=notes.md"]
    assert R._detach_argv(argv, "sz-dry-run") == [
        "--agent",
        "sz-dry-run",
        "review",
        "T1",
        "--",
        "--agent=notes.md",
    ]


def test_detach_argv_is_empty_when_there_is_nothing_to_relaunch(monkeypatch):
    assert R._detach_argv(["--detach"], "sz-dry-run") == []


def test_detach_launches_with_the_resolved_identity(adopted, monkeypatch):
    """A detach with only `DDFLOW_AGENT` set (no `--agent` in argv) relaunches the review
    carrying `--agent <resolved>` before the `review` subcommand, and attributes the job to
    the RESOLVED holder -- so the detached process does not derive the worktree id."""
    monkeypatch.chdir(adopted)
    monkeypatch.setenv("DDFLOW_AGENT", "sz-dry-run")

    captured = {}

    def fake_detach(repo, item, cmd, cwd, agent):
        captured.update(repo=repo, item=item, cmd=cmd, cwd=cwd, agent=agent)
        return O.ok("job", id="J", pid=1, log="/tmp/j.log")

    monkeypatch.setattr(R.AJ, "job_detach", fake_detach)

    c = _ctx(adopted)
    a = argparse.Namespace(_argv=["review", "T1", "--detach"], id=["T1"])
    assert R._detach(a, c) == O.OK

    assert captured["item"] == "T1"
    # The job is attributed to the RESOLVED holder, matching the relaunched process.
    assert captured["agent"] == "sz-dry-run", captured
    tokens = shlex.split(captured["cmd"])
    # The command is `python -m ddflow <argv>`; `--agent <resolved>` precedes the `review`
    # subcommand so the top-level identity flag applies to the detached child.
    assert tokens.index("--agent") < tokens.index("review"), tokens
    assert tokens[tokens.index("--agent") + 1] == "sz-dry-run", tokens
    assert "--detach" not in tokens
