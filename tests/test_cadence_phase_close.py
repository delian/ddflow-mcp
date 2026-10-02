"""A phase cannot complete while a phase-scoped periodic pass is overdue.

Cadences were advisory: `complete <phase>` never consulted them, so an architecture review
due every phase could be skipped forever. A due pass whose unit is phases is now an unmet
condition on completing a phase; running it (`cadence --ran`) or recording a skip with a
reason (`cadence --ran X --note "skipped: ..."`) clears it. Task-counted passes stay
advisory, and a task's completion never asks.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import finish, pass_pipeline, run_cli

OK, REFUSED = 0, 3


def _two_phases(repo):
    (repo / ".ddflow").mkdir(exist_ok=True)
    (repo / ".ddflow" / "config.toml").write_text(
        "[cadence]\narchitecture_review_every_phases = 1\n"
    )
    run_cli(repo, "init")
    for p in ("P0", "P1"):
        run_cli(repo, "phase", "add", p, "--title", p)
        run_cli(repo, "task", "add", f"{p}.T1", "--phase", p, "--globs", f"{p.lower()}/*")
    run_cli(repo, "claim", "P0.T1", "--no-worktree")
    assert finish(repo, "P0.T1")[0] == OK
    pass_pipeline(repo, "P0")
    assert run_cli(repo, "complete", "P0", "--model", "claude-opus-5")[0] == OK
    run_cli(repo, "claim", "P1.T1", "--no-worktree")
    assert finish(repo, "P1.T1")[0] == OK
    pass_pipeline(repo, "P1")


def test_a_phase_with_an_overdue_architecture_review_is_refused_then_passes(repo):
    _two_phases(repo)
    code, _o, err = run_cli(repo, "complete", "P1", "--model", "claude-opus-5")
    assert code == REFUSED and "architecture_review" in err, err
    assert "cadence --ran architecture_review" in err

    assert run_cli(repo, "cadence", "--ran", "architecture_review")[0] == OK
    code, _o, err = run_cli(repo, "complete", "P1", "--model", "claude-opus-5")
    assert code == OK, err


def test_a_recorded_skip_with_a_reason_clears_it(repo):
    _two_phases(repo)
    run_cli(repo, "cadence", "--ran", "architecture_review", "--note", "skipped: no code change")
    assert run_cli(repo, "complete", "P1", "--model", "claude-opus-5")[0] == OK


def test_a_task_completion_never_asks_about_cadences(repo):
    _two_phases(repo)
    run_cli(repo, "task", "add", "P1.T2", "--phase", "P1", "--globs", "z/*")
    run_cli(repo, "claim", "P1.T2", "--no-worktree")
    assert finish(repo, "P1.T2")[0] == OK
