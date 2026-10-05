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


def _ok(r: tuple[int, str, str]) -> None:
    """A CLI setup step is checked and a failure shows its output: B38a14b89e7 failed
    once with only `assert 3 == 0` on `finish`, and an unchecked step before it would
    have been invisible. (`pass_pipeline`'s own gate records are not checked here.)"""
    assert r[0] == OK, r


def _two_phases(repo):
    (repo / ".ddflow").mkdir(exist_ok=True)
    (repo / ".ddflow" / "config.toml").write_text(
        "[cadence]\narchitecture_review_every_phases = 1\n"
    )
    _ok(run_cli(repo, "init"))
    for p in ("P0", "P1"):
        _ok(run_cli(repo, "phase", "add", p, "--title", p))
        _ok(run_cli(repo, "task", "add", f"{p}.T1", "--phase", p, "--globs", f"{p.lower()}/*"))
    _ok(run_cli(repo, "claim", "P0.T1", "--no-worktree"))
    _ok(finish(repo, "P0.T1"))
    pass_pipeline(repo, "P0")
    _ok(run_cli(repo, "complete", "P0", "--model", "claude-opus-5"))
    _ok(run_cli(repo, "claim", "P1.T1", "--no-worktree"))
    _ok(finish(repo, "P1.T1"))
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


def test_a_malformed_calendar_knob_does_not_switch_the_check_off(repo):
    _two_phases(repo)
    cfg = repo / ".ddflow" / "config.toml"
    cfg.write_text(cfg.read_text() + 'every_days = ["bug_hunt=oops"]\n')
    code, _o, err = run_cli(repo, "complete", "P1", "--model", "claude-opus-5")
    assert code == REFUSED and "architecture_review" in err, err


def test_a_malformed_entry_named_for_the_pass_does_not_switch_it_off(repo):
    _two_phases(repo)
    cfg = repo / ".ddflow" / "config.toml"
    cfg.write_text(cfg.read_text() + 'every_days = ["architecture_review=oops"]\n')
    code, _o, err = run_cli(repo, "complete", "P1", "--model", "claude-opus-5")
    assert code == REFUSED and "architecture_review" in err, err


def test_a_good_entry_beside_a_malformed_one_still_enforces_the_count(repo):
    _two_phases(repo)
    cfg = repo / ".ddflow" / "config.toml"
    cfg.write_text(
        cfg.read_text() + 'every_days = ["architecture_review=7", "architecture_review=oops"]\n'
    )
    code, _o, err = run_cli(repo, "complete", "P1", "--model", "claude-opus-5")
    assert code == REFUSED and "architecture_review" in err, err


def test_the_verdict_itself_carries_it_so_every_completion_path_enforces_it(repo):
    # The PR-merge settle path calls CM.verdict directly, not api.complete.
    from ddflow.config import Config
    from ddflow.core.model import State
    from ddflow.services.cadence import phase_overdue

    cfg, st = Config(), State()
    cfg.cadence.architecture_review_every_phases = 1
    st.items["P0"] = type("I", (), {"kind": "phase", "state": "done"})()
    assert "architecture_review" in phase_overdue(st, cfg)[0]
