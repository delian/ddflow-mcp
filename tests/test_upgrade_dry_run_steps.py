"""B-upgrade.12-dry-run: single steps, the lines the mutation sample found surviving, and the doctor verdict.

Split from tests/test_upgrade_dry_run.py (rule r-small-files); helpers live in upgrade_dry_run_support.py.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from upgrade_dry_run_support import (
    clean_fixture,  # noqa: F401  (registered as the module fixture `clean`)
    git,
    options,
    project,
)

from ddflow.services import upgrade_dryrun as DR
from ddflow.services.upgrade_dryrun import _read as _DR_READ

# -- the lines the mutation sample found surviving (U4, 2026-10-10) --------------------


def test_a_shard_that_never_held_still_is_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A shard written while it was read must be REPORTED: the rehearsal cannot treat bytes
    it caught mid-write as the truth, and the mutant silently drops the name."""
    events = tmp_path / ".ddflow" / "events"
    events.mkdir(parents=True)
    for name in ("a.jsonl", "b.jsonl"):
        (events / name).write_bytes(b"{}\n")

    monkeypatch.setattr(
        _DR_READ, "read_stable", lambda p, attempts=3: (b"{}\n", p.name != "a.jsonl")
    )

    data, unstable = DR.shard_bytes_checked(tmp_path)

    assert sorted(data) == ["a.jsonl", "b.jsonl"], "both shards are still returned"
    assert unstable == ["a.jsonl"], "the one that moved is named"


def _step_read_rehearsal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, answer) -> DR.Rehearsal:
    """A rehearsal whose `ddflow` calls all answer `answer`, cloning the fixture itself."""
    root = project(tmp_path, confirm_free=True)
    rehearsal = DR.Rehearsal(options(tmp_path, root))
    rehearsal.clone = lambda which: root  # one clone is enough: the fixture IS the project
    monkeypatch.setattr(DR.Rehearsal, "ddflow", lambda *a, **k: answer)
    return rehearsal


def test_step_read_names_what_a_read_command_did_wrong(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The step's detail is the failures JOINED: which command broke, with its exit and its
    stderr. The mutant leaves the detail empty, so a failed rehearsal explains nothing."""
    boom = DR.P.Captured(returncode=3, stderr="boom\n")
    rehearsal = _step_read_rehearsal(tmp_path, monkeypatch, boom)

    assert rehearsal.step_read() is False

    check = next(c for c in rehearsal.report.checks if c.name == "read with the new code")
    assert "-> 3 boom" in check.detail


def test_step_read_says_what_it_read_when_nothing_broke(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """...and the sentence the passing case shows instead (a passed step that says nothing
    reads like a step that did nothing)."""
    fine = DR.P.Captured(returncode=0)
    rehearsal = _step_read_rehearsal(tmp_path, monkeypatch, fine)

    assert rehearsal.step_read() is True

    check = next(c for c in rehearsal.report.checks if c.name == "read with the new code")
    assert "read commands and" in check.detail and "state unchanged" in check.detail


def test_the_gates_step_names_the_exit_and_the_stderr_of_a_failed_merge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A merge that fails inside the rehearsal must say HOW: its exit status and the tail of
    what it printed, or the operator has a red row and no reason.

    The rehearsal itself is stubbed down to a passing apply, because it is the MESSAGE under
    test, not the rehearsal.
    """
    ok = DR.P.Captured(returncode=0)
    failed = DR.P.Captured(returncode=4, stderr="nothing to merge here\n")
    root = project(tmp_path, confirm_free=True)
    rehearsal = DR.Rehearsal(options(tmp_path, root))
    rehearsal.clone = lambda which: root
    monkeypatch.setattr(DR.Rehearsal, "apply_in", lambda *a, **k: ok)

    def answer(self, cmd, repo, *args, **k):
        return failed if args and args[0] == "merge" else ok

    monkeypatch.setattr(DR.Rehearsal, "ddflow", answer)

    rehearsal.step_agents()

    check = next(c for c in rehearsal.report.checks if c.name == "two agents, one task")
    assert "merge exited 4" in check.detail
    assert "nothing to merge here" in check.detail


def test_the_apply_step_describes_what_held_in_a_clean_rehearsal(clean) -> None:
    """From a real, WHOLE rehearsal (the module fixture): step 4's detail lists what held --
    the records unchanged, the hand edit kept, the second apply a no-op, the restore exact.

    The mutant leaves the detail empty on a passing step, which reads exactly like a step
    that did nothing at all.
    """
    _root, report, _before = clean

    check = next(c for c in report.checks if c.name == "apply with a snapshot, again, restore")
    assert check.status == DR.PASS, check.detail
    assert "users' records unchanged" in check.detail
    assert "second apply a no-op" in check.detail and "restore exact" in check.detail


# -- Bca3a5c599c: the upgrade is judged by the problems it ADDS, not by the old queue's hygiene --


def _doctor_rehearsal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runs: list):
    """A rehearsal whose `doctor` answers come from ``runs`` (exit code, problems) in order:
    the untouched clone first (the baseline), then each check after the upgrade."""
    rehearsal = DR.Rehearsal(options(tmp_path, project(tmp_path, confirm_free=True)))
    monkeypatch.setattr(DR.Rehearsal, "clone", lambda self, name, src=None: tmp_path)

    def fake(self, cmd, repo, *args, **kw):
        code, found = runs.pop(0)
        return DR.P.Captured(code, json.dumps({"problems": found}), "")

    monkeypatch.setattr(DR.Rehearsal, "ddflow", fake)
    return rehearsal


OLD = [
    "orphan_worktree: W1 — INSPECT FIRST — 1 unmerged commit(s).",
    "stuck: AI-roles needs CN-manifest, which is abandoned and will never be done",
]


def test_problems_the_previous_release_already_had_do_not_fail_the_dry_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # exit 1 on 67 old problems both before and after: the old queue's hygiene, not the upgrade
    changed = [OLD[0].replace("1 unmerged", "2 unmerged"), OLD[1]]
    r = _doctor_rehearsal(tmp_path, monkeypatch, [(1, OLD), (1, changed), (1, OLD)])
    assert r.doctor_verdict(tmp_path, "a") == ""
    assert r.doctor_verdict(tmp_path, "b") == ""
    assert r.report.facts["doctor_preexisting"] == 2


def test_a_problem_the_upgrade_adds_fails_the_dry_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    r = _doctor_rehearsal(tmp_path, monkeypatch, [(1, OLD), (1, [*OLD, "lost_events: shard x"])])
    assert "NEW problem" in r.doctor_verdict(tmp_path, "a")


def test_a_doctor_that_crashes_or_prints_nothing_fails_the_dry_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    r = _doctor_rehearsal(tmp_path, monkeypatch, [(1, OLD), (2, []), (1, [])])
    assert "no readable report" in r.doctor_verdict(tmp_path, "a")
    assert "no readable report" in r.doctor_verdict(tmp_path, "b")  # exit 1 with no problem


# -- the fixture git helper isolates a commit from a developer's global git config ------


def test_the_fixture_git_isolates_a_commit_from_a_global_excludesfile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A global core.excludesfile would silently drop the fixture file from the commit
    # (bug Beb7591cc4a): the git() helper must not inherit it.
    exclude = tmp_path / "excludes"
    exclude.write_text("*.txt\n", "utf-8")
    global_cfg = tmp_path / "global.gitconfig"
    global_cfg.write_text(f"[core]\n\texcludesfile = {exclude}\n", "utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_cfg))
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "--quiet", "-b", "main")
    (repo / "kept.txt").write_text("kept\n", "utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "--quiet", "--no-verify", "-m", "fixture")
    tracked = subprocess.check_output(["git", "-C", str(repo), "ls-files"], text=True)
    assert "kept.txt" in tracked


def test_the_fixture_git_isolates_a_commit_from_a_global_gpgsign(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A global commit.gpgsign=true (no signing program) would fail the fixture commit
    # (bug Beb7591cc4a): the git() helper must not inherit it.
    global_cfg = tmp_path / "global.gitconfig"
    global_cfg.write_text("[commit]\n\tgpgsign = true\n[gpg]\n\tprogram = false\n", "utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_cfg))
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "--quiet", "-b", "main")
    (repo / "kept.txt").write_text("kept\n", "utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "--quiet", "--no-verify", "-m", "fixture")
    history = subprocess.check_output(["git", "-C", str(repo), "log", "--format=%s"], text=True)
    assert "fixture" in history
