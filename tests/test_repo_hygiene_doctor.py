"""`ddflow doctor` names the marks a stray process left in the repository's own git state
(B1d82c6b76f): a local identity unlike the global one, core.bare on a checkout with a work
tree, a primary whose index no longer matches its HEAD. Each note carries the repair command."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ddflow.services import repo_hygiene as RH


@pytest.fixture(autouse=True)
def _global_identity(tmp_path_factory, monkeypatch) -> None:
    """The global identity these tests compare with is written here, not assumed."""
    cfg = tmp_path_factory.mktemp("global") / "gitconfig"
    cfg.write_text("[user]\n\tname = Test\n\temail = t@example.com\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout


def test_a_clean_repository_has_no_hygiene_note(repo: Path) -> None:
    assert RH.doctor_notes(repo) == []


def test_a_local_identity_unlike_the_global_one_is_named_with_its_unset_command(
    repo: Path,
) -> None:
    _git(repo, "config", "user.name", "Stray")
    _git(repo, "config", "user.email", "stray@example.com")
    said = "\n".join(RH.doctor_notes(repo))
    assert "user.name = 'Stray'" in said and "user.email = 'stray@example.com'" in said
    assert f"git -C {repo} config --local --unset user.name" in said
    assert f"git -C {repo} config --local --unset user.email" in said


def test_a_local_identity_equal_to_the_global_one_is_not_a_finding(repo: Path) -> None:
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "t@example.com")
    assert RH.doctor_notes(repo) == []


def test_core_bare_on_a_checkout_with_a_work_tree_is_named_with_its_repair(repo: Path) -> None:
    _git(repo, "config", "core.bare", "true")
    [note] = RH.doctor_notes(repo)
    assert "core.bare is true" in note
    assert f"git -C {repo} config --local core.bare false" in note


def test_a_stale_primary_is_named_with_the_restore_command(repo: Path) -> None:
    (repo / "code.py").write_text("old\n")
    _git(repo, "add", "code.py")
    _git(repo, "commit", "-qm", "code")
    _git(repo, "worktree", "add", "-q", "-b", "feat", str(repo.parent / "wt"))
    wt = repo.parent / "wt"
    (wt / "code.py").write_text("new\n")
    _git(wt, "commit", "-qam", "change")
    _git(
        repo,
        "update-ref",
        f"refs/heads/{_git(repo, 'symbolic-ref', '--short', 'HEAD').strip()}",
        "feat",
    )  # main moved, the primary did not follow
    assert (repo / "code.py").read_text() == "old\n"
    [note] = RH.doctor_notes(repo)
    assert "code.py" in note and "stale primary" in note
    assert (
        f"git -C {repo} restore --source=HEAD --staged --worktree -- . ':(exclude).ddflow'" in note
    )
    # from the linked worktree the same primary is named
    assert RH.stale_primary_notes(wt) == RH.stale_primary_notes(repo)
