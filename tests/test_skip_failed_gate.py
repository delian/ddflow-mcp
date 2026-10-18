"""DX-skip-failed-gate (B23bd81462d): a required gate that FAILED is not skippable, an
optional one shows 'skipped after a failed run', and merge refuses a failed or
skipped-after-failure ci gate."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from helpers import git as _git

from ddflow.core.model import fold
from ddflow.infra.log import EventLog


def _setup(repo: Path, required: str = '["implement", "unit_tests", "merge"]') -> None:
    run_cli(repo, "init")
    (repo / ".ddflow" / "config.toml").write_text(
        f"[worktree]\nenabled = false\n[gates]\nrequired = {required}\n"
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ddflow")
    run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", "*.py")
    _git(repo, "checkout", "-q", "-b", "feat")
    (repo / "x.py").write_text("x = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "work")


def _fail(repo: Path, gate: str) -> None:
    code, out, err = run_cli(
        repo, "gate", "record", "T1", gate, "--outcome", "failed", "--reason", "red"
    )
    assert code == 0, out + err


def _outcome(repo: Path, gate: str) -> str:
    return fold(EventLog(repo).read_all(), strict=False).items["T1"].gate_outcome(gate)


def test_skipping_a_failed_required_gate_is_refused(repo):
    _setup(repo)
    _fail(repo, "unit_tests")
    code, out, err = run_cli(repo, "gate", "skip", "T1", "unit_tests", "--reason", "cannot repin")
    assert code == 3 and "not skippable" in out + err, out + err
    assert _outcome(repo, "unit_tests") == "failed"
    code, out, err = run_cli(
        repo, "gate", "record", "T1", "unit_tests", "--outcome", "skipped", "--reason", "x"
    )
    assert code == 3 and _outcome(repo, "unit_tests") == "failed", out + err
    code, out, err = run_cli(
        repo, "gate", "record", "T1", "unit_tests", "--outcome", "unavailable", "--reason", "red"
    )
    assert code == 0 and _outcome(repo, "unit_tests") == "unavailable", out + err


def test_an_optional_failed_gate_may_be_skipped_but_status_says_so(repo):
    _setup(repo)
    _fail(repo, "dedupe")
    code, out, err = run_cli(repo, "gate", "skip", "T1", "dedupe", "--reason", "known")
    assert code == 0, out + err
    code, out, err = run_cli(repo, "gate", "status", "T1")
    assert "skipped after a failed run" in out + err, out + err


def test_merge_refuses_a_failed_or_skipped_after_failure_ci(repo):
    _setup(repo, required='["implement", "merge"]')
    _fail(repo, "ci")
    code, out, err = run_cli(repo, "merge", "T1", "--branch", "feat")
    assert code == 3 and "ci failed" in out + err, out + err
    code, out, err = run_cli(repo, "gate", "skip", "T1", "ci", "--reason", "cannot repin")
    assert code == 0, out + err
    code, out, err = run_cli(repo, "merge", "T1", "--branch", "feat")
    assert code == 3 and "skipped after a failed run" in out + err, out + err


def test_merge_refusal_names_a_way_past_and_recording_unavailable_lifts_it(repo):
    _setup(repo, required='["implement", "merge"]')
    _fail(repo, "ci")
    _, out, err = run_cli(repo, "merge", "T1", "--branch", "feat")
    assert "--outcome unavailable|partial --reason" in out + err, out + err
    code, out, err = run_cli(
        repo, "gate", "record", "T1", "ci", "--outcome", "unavailable", "--reason", "lease"
    )
    assert code == 0, out + err
    _, out, err = run_cli(repo, "merge", "T1", "--branch", "feat")
    assert "ci failed" not in out + err and "skipped after" not in out + err, out + err
