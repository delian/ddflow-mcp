"""A phase cannot close until the full suite has passed on its merged tree
(DX-phase-acceptance-full-suite; D-gate-economy-r3 (a)). A task that merged under it makes
the phase wait for an `integration_tests` pass newer than that merge."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import finish, pass_pipeline, run_cli

from ddflow.infra.log import EventLog

OK, REFUSED = 0, 3


def _phase_with_merged_task(repo):
    for step in (
        ("init",),
        ("phase", "add", "P0", "--title", "P0"),
        ("task", "add", "P0.T1", "--phase", "P0", "--globs", "p0/*"),
        ("claim", "P0.T1", "--no-worktree"),
    ):
        assert run_cli(repo, *step)[0] == OK
    # What `merge` records for the task, without a real worktree.
    EventLog(repo, "agent-test").append("worktree.merged", "P0.T1", {"sha": "abc", "kind": "task"})
    assert finish(repo, "P0.T1")[0] == OK
    pass_pipeline(repo, "P0")


def test_a_phase_with_a_merged_task_is_refused_without_a_full_suite_pass(repo):
    _phase_with_merged_task(repo)
    code, _o, err = run_cli(repo, "complete", "P0", "--model", "claude-opus-5")
    assert code == REFUSED and "full test suite has not passed" in err, err


def test_a_full_suite_pass_after_the_merge_lets_the_phase_close(repo):
    _phase_with_merged_task(repo)
    assert run_cli(repo, "cadence", "--ran", "integration_tests")[0] == OK
    assert run_cli(repo, "complete", "P0", "--model", "claude-opus-5")[0] == OK


def test_a_pass_older_than_the_last_merge_does_not_count(repo):
    _phase_with_merged_task(repo)
    assert run_cli(repo, "cadence", "--ran", "integration_tests")[0] == OK
    EventLog(repo, "agent-test").append("worktree.merged", "P0.T1", {"sha": "def", "kind": "task"})
    code, _o, err = run_cli(repo, "complete", "P0", "--model", "claude-opus-5")
    assert code == REFUSED and "full test suite has not passed" in err, err


def test_off_does_not_ask(repo):
    _phase_with_merged_task(repo)
    run_cli(repo, "config", "schedule.phase_full_suite", "off")
    assert run_cli(repo, "complete", "P0", "--model", "claude-opus-5")[0] == OK


def test_the_knob_defaults_to_require():
    from ddflow.config import Config

    assert Config().schedule.phase_full_suite == "require"


def test_a_pass_stamped_with_the_merge_instant_is_not_newer():
    from types import SimpleNamespace as NS

    from ddflow.services import phase_suite as PS

    st = NS(
        descendants=lambda _p: {"T"},
        cadences={"integration_tests": [{"at": "2026-01-01T00:00:00.5Z"}]},
    )
    merged = [NS(kind="worktree.merged", subject="T", ts="2026-01-01T00:00:00.5Z")]
    assert "has not passed" in PS.missing(merged, st, "P")
    st.cadences["integration_tests"][0]["at"] = "2026-01-01T00:00:00.6Z"
    assert PS.missing(merged, st, "P") == ""
