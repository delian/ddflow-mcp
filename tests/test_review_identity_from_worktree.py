"""`ddflow review --detach` relaunches under the caller's RESOLVED identity, whatever it is
(bug B-review-identity-from-worktree-ignores-as-agent).

An identity held over `as_agent`, `DDFLOW_AGENT` or `ddflow_identify` never rides in the typed
argv, so `_detach_argv` re-declares it. `DDFLOW_AGENT` and the `ddflow_identify` name pattern
both allow a leading dash, and `--agent -dry-run` as two tokens is an argparse error: the
relaunch exits 2 with nothing reviewed. These tests read the relaunch through the real parser, so they pin the
identity the child gets, not how the flag is spelled.
"""

from __future__ import annotations

import argparse
import shlex
import subprocess
from pathlib import Path

import pytest

from ddflow.core import outcome as O
from ddflow.infra.log import default_agent_id
from ddflow.surfaces.cli import build_parser
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


@pytest.fixture
def linked(adopted: Path, tmp_path: Path) -> Path:
    """A real linked worktree of `adopted`: the tree the bug was reported from."""
    tree = tmp_path / "linked-tree"
    git = ["git", "-C", str(adopted)]
    subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "base"], check=True)
    subprocess.run([*git, "worktree", "add", "-q", "-b", "item", str(tree)], check=True)
    return tree


def _ctx(repo: Path, agent: str = "") -> Ctx:
    return Ctx(argparse.Namespace(repo=str(repo), agent=agent, json=False))


def _parsed_agent(argv: list[str]) -> str:
    """The identity the relaunched process would read from `argv`."""
    return build_parser().parse_args(argv).agent


def test_detach_argv_redeclares_an_env_sourced_identity(adopted, monkeypatch):
    monkeypatch.setenv("DDFLOW_AGENT", "sz-dry-run")
    c = _ctx(adopted)
    assert c.log.agent_id == "sz-dry-run", "the identity DDFLOW_AGENT resolved"
    assert R._detach_argv(["review", "T1"], c.log.agent_id) == [
        "--agent=sz-dry-run",
        "review",
        "T1",
    ]


def test_detach_argv_replaces_a_typed_agent(monkeypatch):
    """A `--agent` already typed still relaunches under the RESOLVED holder, not the typed
    token."""
    assert R._detach_argv(["--agent", "flagged", "review", "T1"], "sz-dry-run") == [
        "--agent=sz-dry-run",
        "review",
        "T1",
    ]
    assert R._detach_argv(["--agent=flagged", "review", "T1"], "sz-dry-run") == [
        "--agent=sz-dry-run",
        "review",
        "T1",
    ]


def test_detach_argv_parses_back_to_a_dash_leading_identity():
    """A `-dry-run` value in its own token is read as an option, and the parser exits."""
    assert _parsed_agent(R._detach_argv(["review", "T1"], "-dry-run")) == "-dry-run"


def test_detach_argv_keeps_a_positional_agent_past_the_separator(monkeypatch):
    """A `--agent=...` PAST the `--` end-of-options separator is a positional, not the
    global identity flag, so it stays."""
    argv = ["review", "T1", "--", "--agent=notes.md"]
    assert R._detach_argv(argv, "sz-dry-run") == [
        "--agent=sz-dry-run",
        "review",
        "T1",
        "--",
        "--agent=notes.md",
    ]


def test_detach_argv_is_empty_when_there_is_nothing_to_relaunch(monkeypatch):
    assert R._detach_argv(["--detach"], "sz-dry-run") == []


def test_review_help_names_the_agent_flag():
    """`ddflow review --help` documents `--agent`, so an agent running it from a shell can
    attribute the review to its own name (`as_agent`/`--agent`) instead of a derived
    `{host}-{tree}` identity (bug Bbf6e536827)."""
    root = build_parser()
    review = next(
        sub
        for action in root._actions
        if isinstance(action, argparse._SubParsersAction)
        for name, sub in action.choices.items()
        if name == "review"
    )
    assert "--agent AGENT" in review.format_help()


@pytest.mark.parametrize("identity", ["sz-dry-run", "-dry-run"])
def test_detach_launches_with_the_resolved_identity(linked, monkeypatch, identity):
    """A detach with only `DDFLOW_AGENT` set (no `--agent` in argv) launches a command whose
    own parser yields that identity, and attributes the job to the same holder, standing in a
    linked worktree where the tree-derived default would otherwise answer."""
    monkeypatch.chdir(linked)
    monkeypatch.setenv("DDFLOW_AGENT", identity)

    captured = {}

    def fake_detach(repo, item, cmd, cwd, agent):
        captured.update(repo=repo, item=item, cmd=cmd, cwd=cwd, agent=agent)
        return O.ok("job", id="J", pid=1, log="/tmp/j.log")

    monkeypatch.setattr(R.AJ, "job_detach", fake_detach)

    c = _ctx(linked)
    a = argparse.Namespace(_argv=["review", "T1", "--detach"], id=["T1"])
    assert R._detach(a, c) == O.OK

    assert captured["item"] == "T1"
    assert captured["agent"] == identity != default_agent_id(linked), captured
    tokens = shlex.split(captured["cmd"])
    assert "--detach" not in tokens
    # The command is `python -m ddflow <argv>`: everything past the module is the child's argv.
    assert _parsed_agent(tokens[tokens.index("-m") + 2 :]) == identity, tokens
