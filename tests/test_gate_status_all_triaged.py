"""Bf9554e4156: `ddflow gate status` showed `[!]` for a review gate whose findings were
all triaged, and never said it no longer blocks completion.

A failed rubber_duck/critic whose findings are all settled (0 untriaged) is answered:
`ddflow complete` no longer counts it as a blocker (its confirmed findings were cleared by
later delta rounds), but `gate status` kept printing `[!] ... failed` and `next = <review
gate>`, so an agent re-ran rounds that could not change anything. The status must mark such
a gate "all findings triaged: not blocking" and move past it.
"""

from __future__ import annotations

import stat
from pathlib import Path

import ddflow.api.review as api
from conftest import run_cli
from helpers import git as _git


def _setup(repo: Path, tmp_path: Path) -> None:
    """T1 with a minimal pipeline (critic first); the fake reviewer flags x.py's FINDME."""
    cli = tmp_path / "fake-reviewer"
    cli.write_text(
        "#!/bin/sh\nin=$(cat | grep '^+')\n"
        "case \"$in\" in *FINDME*) printf 'FINDING HIGH x.py:1\\nbad\\n\\nSTATUS: FINDINGS 1\\n';; "
        "*) echo 'STATUS: NO FINDINGS';; esac\n"
    )
    cli.chmod(cli.stat().st_mode | stat.S_IXUSR)
    run_cli(repo, "init")
    (repo / ".ddflow" / "config.toml").write_text(
        '[gates]\ntask_pipeline = ["critic", "unit_tests", "merge"]\n'
        'required = ["unit_tests", "merge"]\n'
        "[worktree]\nenabled = false\n"
        "[review]\ndelta_default = true\nmax_rounds = 0\n"
        f'[[reviewer]]\nname = "fake"\nkind = "command"\ncommand = "{cli}"\n'
        'model = "gemini-2.5-pro"\ngates = ["critic"]\n'
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ddflow")
    run_cli(repo, "task", "add", "T1", "--title", "add files", "--globs", "*.py")
    _git(repo, "checkout", "-q", "-b", "feat")
    (repo / "x.py").write_text("x = 1  # FINDME\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "work")


def test_a_refuted_failed_review_is_marked_not_blocking(repo, tmp_path):
    """A FAILED review whose findings are all refuted blocks `complete` no more, and the
    status line says so instead of stalling `next` at the review gate."""
    _setup(repo, tmp_path)
    out = api.review(repo, gate="critic", item="T1", branch="feat")
    assert out.data["outcome"] == "failed", out.data
    t = api.triage(repo, "T1", gate="critic", finding=1, verdict="refuted", probe="ran it: fine")
    assert t.exit == 0, t.reason
    code, text, err = run_cli(repo, "gate", "status", "T1")
    assert code == 0, text + err
    assert "next = critic" not in text, text
    assert "all findings triaged: not blocking" in text, text


def test_an_untriaged_failed_review_still_holds_the_gate(repo, tmp_path):
    """The note is precision, not a blanket pass: an finding with no verdict is still a
    blocker, so `next` stays at the review gate and the line does not claim otherwise."""
    _setup(repo, tmp_path)
    out = api.review(repo, gate="critic", item="T1", branch="feat")
    assert out.data["outcome"] == "failed", out.data
    code, text, err = run_cli(repo, "gate", "status", "T1")
    assert code == 0, text + err
    assert "next = critic" in text, text
    assert "all findings triaged: not blocking" not in text, text
