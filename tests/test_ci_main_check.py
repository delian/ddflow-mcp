"""Post-merge health of the base: `[ci].on_merge`, `ci.result`, `ddflow ci record` (B-ci-main-check)."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import run_cli

from ddflow.api import ci as A
from ddflow.config import Config
from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.services import ci as CI


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def fake_precommit(tmp_path, monkeypatch):
    """A `pre-commit` that fails while BAD.txt exists and records the SKIP it was given."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    pc = bin_dir / "pre-commit"
    pc.write_text(
        "#!/bin/sh\n"
        'echo "$SKIP" > "$TMPDIR_SKIP" 2>/dev/null\n'
        "if [ -e BAD.txt ]; then echo 'ruff check.....................Failed'; exit 1; fi\n"
        "echo 'ruff check.....................Passed'\n"
    )
    pc.chmod(pc.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return pc


def _project(repo, *, bad=False):
    run_cli(repo, "init")
    (repo / ".pre-commit-config.yaml").write_text("repos: []\n")
    if bad:
        (repo / "BAD.txt").write_text("x\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "adopt")
    return _git(repo, "rev-parse", "HEAD")


def _state(repo):
    return fold(EventLog(repo).read_all(), strict=False)


def test_a_failing_base_is_recorded_and_files_one_bug_and_fix_task_per_check(repo, fake_precommit):
    sha = _project(repo, bad=True)
    out = A.check_after_merge(repo, sha=sha, item="T1")
    assert out["status"] == "failed" and out["failed"] == ["ruff check"]
    assert out["bugs"] == [CI.bug_id("ruff check")]
    st = _state(repo)
    assert st.ci_results[-1]["stage"] == "merge" and st.ci_results[-1]["ok"] is False
    bug = st.bugs[CI.bug_id("ruff check")]
    assert bug.open and bug.fix_task and bug.fix_task in st.items
    # the same failure on the next merge is the same bug, not a second one
    again = A.check_after_merge(repo, sha=sha, item="T2")
    assert again["bugs"] == [] and len([b for b in _state(repo).bugs if b.startswith("Bci-")]) == 1


def test_a_check_that_fails_again_after_its_bug_was_fixed_is_a_new_bug(repo, fake_precommit):
    sha = _project(repo, bad=True)
    A.check_after_merge(repo, sha=sha)
    EventLog(repo).append("bug.fixed", CI.bug_id("ruff check"), {"sha": sha})
    assert not _state(repo).bugs[CI.bug_id("ruff check")].open
    out = A.check_after_merge(repo, sha=sha)
    assert out["bugs"] == [f"{CI.bug_id('ruff check')}-{sha[:7]}"]


def test_a_healthy_base_is_recorded_and_files_nothing(repo, fake_precommit):
    sha = _project(repo)
    out = A.check_after_merge(repo, sha=sha)
    assert out["status"] == "passed" and out["bugs"] == []
    st = _state(repo)
    assert st.ci_results[-1]["ok"] is True and not st.bugs


def test_on_merge_off_runs_nothing_and_records_nothing(repo, fake_precommit):
    sha = _project(repo, bad=True)
    (repo / ".ddflow" / "config.toml").write_text('[ci]\non_merge = "off"\n')
    assert A.check_after_merge(repo, sha=sha) == {}
    assert _state(repo).ci_results == []


def test_an_unknown_on_merge_mode_is_said_not_ignored(repo, fake_precommit):
    sha = _project(repo)
    (repo / ".ddflow" / "config.toml").write_text('[ci]\non_merge = "sometimes"\n')
    out = A.check_after_merge(repo, sha=sha)
    assert out["status"] == "unavailable" and "off | fast | full" in out["why"]


def test_a_project_without_a_ci_command_is_left_alone(repo):
    run_cli(repo, "init")
    assert A.check_after_merge(repo) == {}
    assert _state(repo).ci_results == []


def test_fast_skips_the_test_hooks_of_the_default_command_and_full_does_not(repo, fake_precommit):
    _project(repo)
    cfg = Config.load(repo)
    assert cfg.ci.on_merge == "fast"
    assert CI.main_command(repo, cfg)[0].startswith(f"SKIP={CI.FAST_SKIP} pre-commit run")
    cfg.ci.on_merge = "full"
    assert CI.main_command(repo, cfg)[0].startswith("pre-commit run")
    cfg.ci.on_merge, cfg.ci.command = "fast", "make check"
    assert CI.main_command(repo, cfg)[0] == "make check"  # the operator's command, as written


def test_ci_record_writes_the_outcome_a_hook_saw_with_its_failing_checks(repo, tmp_path):
    run_cli(repo, "init")
    report = tmp_path / "out.txt"
    report.write_text("ruff check.......Failed\nbandit.......Passed\n")
    code, out, _ = run_cli(
        repo, "ci", "record", "--stage", "pre-push", "--result", "failed", "--report", str(report)
    )
    assert code == 0, out
    last = _state(repo).ci_results[-1]
    assert last["stage"] == "pre-push" and not last["ok"]
    assert [c["id"] for c in last["checks"] if not c["ok"]] == ["ruff check"]
    assert run_cli(repo, "ci", "record", "--stage", "pre-push")[0] == 1  # no --result
    assert run_cli(repo, "ci", "record", "--stage", "nope", "--result", "passed")[0] != 0
    json.dumps(last)


def test_the_merge_verb_reports_the_base_health(repo, fake_precommit):
    _project(repo, bad=True)
    run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", "w.txt")
    assert run_cli(repo, "claim", "T1")[0] == 0
    wt = fold(EventLog(repo).read_all(), strict=False).items["T1"].worktree
    wt = (repo / wt).resolve()
    (wt / "w.txt").write_text("w\n")
    _git(wt, "add", "-A")
    _git(wt, "commit", "-qm", "work")
    code, out, err = run_cli(repo, "--json", "merge", "T1")
    assert '"ci"' in out and CI.bug_id("ruff check") in out, (code, out[-600:], err[-300:])


def test_the_pre_push_hook_reports_its_outcome_to_ddflow(repo, fake_precommit, tmp_path):
    """The hook runs the checks in a scratch worktree and `ddflow ci record`s how it went."""
    sha = _project(repo, bad=True)
    shim = tmp_path / "bin" / "ddflow"
    shim.write_text(f'#!/bin/sh\nexec {sys.executable} -m ddflow --repo "{repo}" "$@"\n')
    shim.chmod(shim.stat().st_mode | stat.S_IXUSR)
    hook = Path(__file__).resolve().parents[1] / "scripts" / "ci" / "pre-push"
    env = {
        **os.environ,
        "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
    }
    p = subprocess.run(
        ["bash", str(hook), "origin"],
        input=f"refs/heads/main {sha} refs/heads/main {'0' * 40}\n",
        cwd=repo, env=env, capture_output=True, text=True, check=False,
    )  # fmt: skip
    assert p.returncode == 1, p.stdout + p.stderr  # a failed hook still refuses the push
    last = _state(repo).ci_results[-1]
    assert last["stage"] == "pre-push" and not last["ok"] and last["sha"] == sha
    assert [c["id"] for c in last["checks"] if not c["ok"]] == ["ruff check"]


def test_a_regression_that_keeps_failing_across_merges_is_one_open_bug(repo, fake_precommit):
    sha = _project(repo, bad=True)
    A.check_after_merge(repo, sha=sha)
    first = CI.bug_id("ruff check")
    EventLog(repo).append("bug.fixed", first, {"sha": sha})
    second = A.check_after_merge(repo, sha=sha)["bugs"]
    third = A.check_after_merge(repo, sha=sha[::-1])["bugs"]  # a later merge, still failing
    assert len(second) == 1 and third == []
    assert len([b for b in _state(repo).bugs.values() if b.open]) == 1


def test_long_check_ids_with_a_shared_prefix_are_different_bugs():
    a = "tests/test_services/test_ci.py::test_main_command_off"
    b = "tests/test_services/test_ci.py::test_main_command_full"
    assert CI.bug_id(a) != CI.bug_id(b) and CI.bug_id(a) == CI.bug_id(a)


def test_main_command_says_there_is_none_when_off(repo, fake_precommit):
    _project(repo)
    cfg = Config.load(repo)
    cfg.ci.on_merge = "off"
    assert CI.main_command(repo, cfg) == ("", "[ci].on_merge is off")
