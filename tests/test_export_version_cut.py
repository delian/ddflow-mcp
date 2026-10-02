"""``ddflow version cut --changelog`` (B-export-version-cut): the new version's section is
written through the changelog export as part of the cut, on a real git repo."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest
from conftest import run_cli
from fakeforge import STATE_ENV, Forge, install

from ddflow.infra import worktree as W
from ddflow.infra.log import EventLog

REMOTE_URL = "https://example.com/acme/proj"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def _commit(repo: Path, name: str, msg: str) -> str:
    (repo / name).write_text(name)
    _git(repo, "add", name)
    _git(repo, "commit", "-qm", msg)
    return _git(repo, "rev-parse", "HEAD")


def _done(repo: Path, item: str, title: str, sha: str) -> None:
    log = EventLog(repo, "tester")
    log.append("task.added", item, {"title": title, "kind": "task", "tags": []})
    log.append("item.completed", item, {"sha": sha})


@pytest.fixture
def proj(repo):
    """main: baseline v1.0.0, then two done tasks (one feat, one fix) not yet released."""
    run_cli(repo, "init")
    _git(repo, "remote", "add", "origin", REMOTE_URL + ".git")
    _git(repo, "add", ".ddflow")
    _git(repo, "commit", "-qm", "chore: adopt ddflow")
    _git(repo, "tag", "-a", "v1.0.0", "-m", "v1.0.0")
    _done(repo, "T1", "Shiny new thing. More words.", _commit(repo, "a.py", "feat: shiny"))
    _done(repo, "T2", "Repair the thing", _commit(repo, "b.py", "fix: repair"))
    return repo


def _cut(repo: Path, *extra: str):
    return run_cli(repo, "--json", "version", "cut", "--version", "1.1.0", *extra)


def test_cut_with_changelog_moves_unreleased_into_the_new_section(proj):
    repo = proj
    code, out, err = _cut(repo, "--changelog")
    assert code == 0, err + out
    assert json.loads(out)["changelog"] == "CHANGELOG.md"
    text = _git(repo, "show", "v1.1.0:CHANGELOG.md")  # in the commit the tag names
    assert re.search(r"^## \[1\.1\.0\] - \d{4}-\d\d-\d\d$", text, re.M), text
    section = text.split("## [1.1.0]")[1]
    assert "- Shiny new thing" in section and "- Repair the thing" in section
    unreleased = text.split("## [Unreleased]")[1].split("## [1.1.0]")[0]
    assert "- " not in unreleased, "Unreleased must be empty after the cut"
    assert f"[1.1.0]: {REMOTE_URL}/compare/v1.0.0...v1.1.0" in text
    assert f"[Unreleased]: {REMOTE_URL}/compare/v1.1.0...HEAD" in text
    assert _git(repo, "status", "--porcelain", "--untracked-files=no") == ""
    assert (repo / "CHANGELOG.md").read_text().strip() == text, (
        "the checkout holds what the tag has"
    )
    assert "Shiny new thing" in _git(repo, "tag", "-l", "-n9", "v1.1.0"), "tag notes = the slice"
    assert _git(repo, "tag", "-l") == "v1.0.0\nv1.1.0", "the temporary render tag is gone"
    code, out, err = run_cli(repo, "export", "changelog", "--check")
    assert code == 0, f"the written file is not what export would write now: {out}{err}"


def test_without_the_flag_nothing_is_written(proj):
    repo = proj
    code, _out, err = _cut(repo)
    assert code == 0, err
    assert not (repo / "CHANGELOG.md").exists()
    assert _git(repo, "ls-tree", "-r", "--name-only", "v1.1.0").count("CHANGELOG.md") == 0
    assert _git(repo, "log", "-1", "--format=%s") == "fix: repair"


def test_dry_run_with_changelog_writes_nothing(proj):
    repo = proj
    code, out, err = _cut(repo, "--changelog", "--dry-run")
    assert code == 0, err
    assert "would write CHANGELOG.md" in out
    assert not (repo / "CHANGELOG.md").exists()
    assert _git(repo, "tag", "-l") == "v1.0.0"


def test_dry_run_refuses_what_the_real_cut_refuses(proj):
    repo = proj
    (repo / "CHANGELOG.md").write_text("# my own file\n")
    _git(repo, "add", "CHANGELOG.md")
    _git(repo, "commit", "-qm", "docs: own changelog")
    code, out, err = _cut(repo, "--changelog", "--dry-run")
    assert code == 3, out + err
    assert (repo / "CHANGELOG.md").read_text() == "# my own file\n"


def test_a_hand_edited_changelog_is_refused_without_force(proj):
    repo = proj
    assert _cut(repo, "--changelog")[0] == 0
    path = repo / "CHANGELOG.md"
    path.write_text(path.read_text() + "\nA hand-written note.\n")
    _git(repo, "commit", "-qam", "docs: note")
    _done(repo, "T3", "Third change", _commit(repo, "c.py", "feat: third"))
    code, out, err = run_cli(repo, "--json", "version", "cut", "--version", "1.2.0", "--changelog")
    assert code == 3, out + err
    assert "edited by hand" in out + err
    assert _git(repo, "tag", "-l") == "v1.0.0\nv1.1.0", "a refused cut must not tag"
    code, out, err = run_cli(
        repo, "--json", "version", "cut", "--version", "1.2.0", "--changelog", "--force"
    )
    assert code == 0, out + err
    assert "A hand-written note." not in _git(repo, "show", "v1.2.0:CHANGELOG.md")


def test_uncommitted_edits_to_the_changelog_are_not_swept_into_the_cut(proj):
    repo = proj
    (repo / "CHANGELOG.md").write_text("mine\n")
    code, out, err = _cut(repo, "--changelog")
    assert code == 3 and "uncommitted" in out + err
    assert (repo / "CHANGELOG.md").read_text() == "mine\n"


def test_region_mode_moves_unreleased_below_the_markers(proj):
    repo = proj
    with (repo / ".ddflow" / "config.toml").open("a") as fh:
        fh.write('\n[export.changelog]\nmode = "region"\n')
    from ddflow.services.export import write

    old = (
        "# Changelog\n\nIntro by hand.\n\n"
        + write.region_text("changelog", "## [Unreleased]\n")
        + "\n## [1.0.0] - 2026-01-01\n\n- First.\n\n"
        + f"[Unreleased]: {REMOTE_URL}/compare/v1.0.0...HEAD\n"
    )
    (repo / "CHANGELOG.md").write_text(old)
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "docs: changelog")
    code, out, err = _cut(repo, "--changelog")
    assert code == 0, out + err
    text = _git(repo, "show", "v1.1.0:CHANGELOG.md")
    assert text.startswith("# Changelog\n\nIntro by hand.\n"), "text outside the region is kept"
    assert text.index("## [Unreleased]") < text.index("## [1.1.0]") < text.index("## [1.0.0]")
    assert "- Shiny new thing" in text.split("## [1.1.0]")[1].split("## [1.0.0]")[0]
    assert "- First." in text
    assert f"[Unreleased]: {REMOTE_URL}/compare/v1.1.0...HEAD" in text
    assert f"[1.1.0]: {REMOTE_URL}/compare/v1.0.0...v1.1.0" in text


def test_a_git_failure_is_exit_2_and_leaves_no_tag(proj, monkeypatch):
    from ddflow.api import flow as A
    from ddflow.services import changelog_cut as CC

    real = CC._git

    def broken(repo, *args):
        if args and args[0] == "tag":
            return W.GitResult(1, "", "boom")
        return real(repo, *args)

    monkeypatch.setattr(CC, "_git", broken)
    out = A.version_cut(proj, version="1.1.0", changelog=True, agent="tester")
    assert out.exit == 2, out
    assert _git(proj, "tag", "-l") == "v1.0.0"
    assert not (proj / "CHANGELOG.md").exists()


def test_changelog_in_append_mode_is_refused(proj):
    repo = proj
    with (repo / ".ddflow" / "config.toml").open("a") as fh:
        fh.write('\n[export.changelog]\nmode = "append"\n')
    code, out, err = _cut(repo, "--changelog")
    assert code == 3 and "append" in out + err


# -- pull requests: the file travels in the release request -------------------------------


@pytest.fixture
def gitflow_pr(repo, tmp_path, monkeypatch):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    _git(repo, "remote", "add", "origin", str(remote))
    run_cli(repo, "init")
    with (repo / ".ddflow" / "config.toml").open("a") as fh:
        fh.write('\n[flow]\nmodel = "gitflow"\nintegration = "pr"\nforge = "github"\n')
    _git(repo, "add", ".ddflow")
    _git(repo, "commit", "-qm", "chore: adopt ddflow")
    _git(repo, "tag", "-a", "v1.0.0", "-m", "v1.0.0")
    _git(repo, "branch", "develop")
    _git(repo, "checkout", "-q", "develop")
    _git(repo, "push", "-q", "origin", "main", "develop", "v1.0.0")
    bindir, state = install(tmp_path, remote)
    monkeypatch.setenv("PATH", f"{bindir}:{Path('/usr/bin')}:{Path('/bin')}")
    monkeypatch.setenv(STATE_ENV, str(state))
    return repo, Forge(state), remote


def test_pr_mode_carries_the_changelog_in_the_release_request(gitflow_pr):
    repo, forge, remote = gitflow_pr
    _done(repo, "T1", "Shiny new thing", _commit(repo, "a.py", "feat: shiny"))
    _git(repo, "push", "-q", "origin", "develop")
    code, out, err = _cut(repo, "--changelog")
    assert code == 0, out + err
    head = _git(remote, "show", "release/1.1.0:CHANGELOG.md")
    assert "## [1.1.0]" in head and "- Shiny new thing" in head
    assert "Shiny new thing" in forge.pr(1)["body"], "the request body is the version's section"
    assert _git(repo, "tag", "-l") == "v1.0.0", "the tag waits for the merged request"
    assert not (repo / "CHANGELOG.md").exists(), "develop's checkout is not touched"
    # without the flag, the release branch carries no changelog
    code, _o, _e = run_cli(repo, "--json", "version", "cut", "--version", "1.2.0")
    assert code == 0
    assert (
        subprocess.run(
            ["git", "-C", str(remote), "cat-file", "-e", "release/1.2.0:CHANGELOG.md"],
            capture_output=True,
        ).returncode
        != 0
    )
