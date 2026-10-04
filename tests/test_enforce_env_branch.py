"""B182: the commit hook refuses commits made directly on an [flow].environments branch.

GitLab flow's "upstream first": an environment receives work by promotion only. A direct
commit there is work upstream never had, and the next promotion conflicts, late. Real git
repository, real hook.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.services import enforce as E


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=check,
        env={**os.environ, "DDFLOW_AGENT": "alpha"},
        timeout=180,
    )


def _setup(repo: Path, extra: str = "") -> Path:
    run_cli(repo, "adopt", "--agents", "claude")
    (repo / ".ddflow" / "config.toml").write_text(
        '[enforce]\ncommit_without_lease = "off"\n'
        + extra
        + '\n[flow]\nenvironments = ["production"]\n'
    )
    (repo / "a.txt").write_text("1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "scaffold", "--no-verify")
    _git(repo, "branch", "production")
    return repo


def test_a_direct_commit_on_an_environment_branch_is_refused(repo):
    _setup(repo)
    _git(repo, "switch", "-q", "production")
    (repo / "a.txt").write_text("hotfix\n")
    _git(repo, "add", "a.txt")
    r = _git(repo, "commit", "-qm", "direct", check=False)
    assert r.returncode != 0, "the hook allowed a commit on an environment branch"
    assert "production" in r.stderr and "promot" in r.stderr
    assert "environment_commits" in r.stderr, "the refusal must name the opt-out"


def test_the_same_commit_on_the_trunk_is_allowed(repo):
    _setup(repo)
    (repo / "a.txt").write_text("work\n")
    _git(repo, "add", "a.txt")
    assert _git(repo, "commit", "-qm", "on main", check=False).returncode == 0


def test_a_merge_into_an_environment_branch_is_a_promotion_and_passes(repo):
    _setup(repo)
    (repo / "a.txt").write_text("work\n")
    _git(repo, "commit", "-qam", "on main", "--no-verify")
    _git(repo, "switch", "-q", "production")
    r = _git(repo, "merge", "--no-ff", "-m", "promote", "main", check=False)
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.parametrize("mode,allowed", [("off", True), ("warn", True), ("block", False)])
def test_the_policy_knob(repo, mode, allowed):
    _setup(repo, f'environment_commits = "{mode}"\n')
    _git(repo, "switch", "-q", "production")
    (repo / "a.txt").write_text("hotfix\n")
    _git(repo, "add", "a.txt")
    r = _git(repo, "commit", "-qm", "direct", check=False)
    assert (r.returncode == 0) is allowed, r.stderr
    if mode == "warn":
        assert "environment branch" in r.stderr


def test_no_environments_means_no_check(repo):
    run_cli(repo, "adopt", "--agents", "claude")
    (repo / ".ddflow" / "config.toml").write_text('[enforce]\ncommit_without_lease = "off"\n')
    _git(repo, "switch", "-q", "-c", "production")
    (repo / "a.txt").write_text("x\n")
    _git(repo, "add", "a.txt")
    assert _git(repo, "commit", "-qm", "x", check=False).returncode == 0


def test_check_commit_unit(repo):
    _setup(repo)
    _git(repo, "switch", "-q", "production")
    (repo / "a.txt").write_text("hotfix\n")
    _git(repo, "add", "a.txt")
    code, msg = E.check_commit(repo)
    assert code == 1 and "environment branch" in msg


def test_a_squash_merge_commit_is_a_promotion_and_passes(repo):
    _setup(repo)
    _git(repo, "switch", "-q", "-c", "work")
    (repo / "a.txt").write_text("work\n")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-qm", "work", "--no-verify")
    _git(repo, "switch", "-q", "production")
    _git(repo, "merge", "-q", "--squash", "work")
    r = _git(repo, "commit", "-qm", "promote work (squash)", check=False)
    assert r.returncode == 0, r.stderr


def test_the_warn_message_names_the_actual_mode_not_block(repo):
    _setup(repo, 'environment_commits = "warn"\n')
    _git(repo, "switch", "-q", "production")
    (repo / "a.txt").write_text("hotfix\n")
    _git(repo, "add", "a.txt")
    code, msg = E.check_commit(repo)
    assert code == 0 and 'environment_commits = "warn"' in msg and '"block"' not in msg
    assert "refusing" not in msg and "warning: a commit made directly" in msg
