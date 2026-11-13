"""An agent whose review-round budget is spent has an approval route (bug B6cbaeebc00).

`ddflow review --force --reason` past [review].max_rounds refused with a message that told
the agent to "ask the operator for `ddflow review ... --force --reason`", but `ddflow review`
accepted no `--approval` flag — passing it was an argparse error, so an agent could not file
the operator approval the way `gate skip` and `merge` can. The refusal now files a
`review.force` approval with `--approval request` and the operator answers it in chat.
"""

from __future__ import annotations

import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pytest
from conftest import run_cli
from helpers import git as _git

import ddflow.api.review as api

OK, REFUSED = 0, 3


@pytest.fixture(autouse=True)
def _every_review_is_a_full_round(monkeypatch):
    monkeypatch.setenv("DDFLOW_REVIEW_DELTA_DEFAULT", "0")


def _setup(repo: Path, tmp_path: Path) -> Path:
    seen = tmp_path / "seen.txt"
    cli = tmp_path / "fake-reviewer"
    cli.write_text(
        "#!/bin/sh\nin=$(cat)\n"
        f"printf 'call\\n' >> '{seen}'\n"
        "case \"$in\" in *FINDME*) printf 'FINDING HIGH x.py:1\\nbad\\n\\nSTATUS: FINDINGS 1\\n';; "
        "*) echo 'STATUS: NO FINDINGS';; esac\n"
    )
    cli.chmod(cli.stat().st_mode | stat.S_IXUSR)
    run_cli(repo, "init")
    (repo / ".ddflow" / "config.toml").write_text(
        "[worktree]\nenabled = false\n"
        f'[[reviewer]]\nname = "fake"\nkind = "command"\ncommand = "{cli}"\n'
        'model = "gemini-2.5-pro"\ngates = ["critic", "rubber_duck"]\n'
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ddflow")
    run_cli(repo, "task", "add", "T1", "--title", "add files", "--globs", "*.py")
    (repo / "x.py").write_text("x = 1  # FINDME\n")
    return seen


def _spend_the_budget(repo: Path) -> None:
    for _ in (1, 2):
        out = api.review(repo, gate="critic", item="T1")
        assert out.data["outcome"] == "failed", out.reason


def test_an_agent_can_file_a_review_force_approval(repo, tmp_path):
    """The exact command in the bug's repro no longer dies on argparse: it files a request."""
    _setup(repo, tmp_path)
    _spend_the_budget(repo)
    code, out, err = run_cli(
        repo,
        "review", "T1", "--gate", "critic", "--force", "--reason", "x", "--approval", "request",
        agent="bot",
    )
    assert "unrecognized arguments" not in err, err
    assert code == REFUSED, (code, err)
    assert "review.force" in err, err
    assert "approval" in err, err  # the route it files and names


def test_an_agents_force_without_an_approval_steers_not_runs(repo, tmp_path):
    """An agent's bare `--force --reason` past the cap is refused and steers to --approval."""
    seen = _setup(repo, tmp_path)
    _spend_the_budget(repo)
    before = seen.read_text().count("call")
    out = api.review(repo, gate="critic", item="T1", force=True, reason="x", agent="bot")
    assert out.exit == REFUSED, out.reason
    assert "review.force" in out.reason, out.reason
    assert seen.read_text().count("call") == before, "a reviewer was called past the cap unapproved"
