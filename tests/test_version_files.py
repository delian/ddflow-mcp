"""`version cut` bumps the project's version files (B174).

`[flow.version_files]` maps a path to a regex with ONE capture group -- the version text.
The bump is a commit: on the branch the tag names for a trunk cut, on the release branch
(so inside the release request, in pr mode) under gitflow. A tag whose commit still says
the old version was the problem.
"""

# ruff: noqa: F811  (fixtures imported from test_flow)
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from conftest import run_cli
from test_flow import _commit, _git, _state, gitflow, gitflow_pr, pr_repo  # noqa: F401

PYPROJECT = '[project]\nname = "demo"\nversion = "0.0.1"\n'
PACKAGE = '{\n  "name": "demo",\n  "version": "0.0.1"\n}\n'


def _configure(repo: Path, **files: str) -> None:
    """Append [flow.version_files] (the [flow] table may already exist)."""
    with (repo / ".ddflow" / "config.toml").open("a") as fh:
        fh.write("\n[flow.version_files]\n")
        for path, pattern in files.items():
            fh.write(f"'{path.replace('__', '.')}' = '{pattern}'\n")


PY = r'^version = "([^"]*)"$'
JS = r'"version": "([^"]*)"'


def _trunk(repo, py: str = PY):
    run_cli(repo, "init")
    (repo / "pyproject.toml").write_text(PYPROJECT)
    (repo / "package.json").write_text(PACKAGE)
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "chore: adopt ddflow with version files")
    _commit(repo, "a.py", "a\n", "feat: a")
    _configure(repo, pyproject__toml=py, package__json=JS)
    _git(repo, "add", ".ddflow")
    _git(repo, "commit", "-qm", "chore: version files")
    return repo


def test_a_trunk_cut_commits_the_bump_the_tag_names(repo):
    _trunk(repo)
    code, out, err = run_cli(repo, "--json", "version", "cut", "--version", "1.2.3")
    assert code == 0, err
    body = json.loads(out)
    assert body["tag"] == "v1.2.3"
    assert 'version = "1.2.3"' in _git(repo, "show", "v1.2.3:pyproject.toml")
    assert '"version": "1.2.3"' in _git(repo, "show", "v1.2.3:package.json")
    assert 'name = "demo"' in _git(repo, "show", "v1.2.3:pyproject.toml"), "only the group"
    assert _git(repo, "log", "-1", "--format=%s", "v1.2.3") == "chore: bump version to 1.2.3"
    assert not _git(repo, "status", "--porcelain", "--untracked-files=no"), "left the tree dirty"
    assert sorted(body["version_files"]) == ["package.json", "pyproject.toml"]


def test_a_dry_run_writes_nothing_and_says_what_it_would_bump(repo):
    _trunk(repo)
    head = _git(repo, "rev-parse", "HEAD")
    code, out, err = run_cli(repo, "--json", "version", "cut", "--version", "1.2.3", "--dry-run")
    assert code == 0, err
    assert any("pyproject.toml" in s for s in json.loads(out)["steps"]), out
    assert (
        _git(repo, "rev-parse", "HEAD") == head and "0.0.1" in (repo / "pyproject.toml").read_text()
    )
    assert (
        subprocess.run(
            ["git", "-C", str(repo), "tag", "-l", "v1.2.3"], capture_output=True, text=True
        ).stdout
        == ""
    )


def test_a_pattern_that_matches_nothing_refuses_before_any_tag(repo):
    _trunk(repo, py=r'^ver = "([^"]*)"$')
    code, out, err = run_cli(repo, "--json", "version", "cut", "--version", "1.2.3")
    assert code == 3 and "matches nothing" in json.loads(out)["warning"] + err, (out, err)
    assert _git(repo, "tag", "-l") == "", "a refused cut must leave no tag"


def test_more_than_one_match_is_refused_not_guessed(repo):
    _trunk(repo)
    (repo / "pyproject.toml").write_text(PYPROJECT + '[tool.x]\nversion = "9.9.9"\n')
    _git(repo, "commit", "-qam", "chore: second version line")
    code, out, err = run_cli(repo, "--json", "version", "cut", "--version", "1.2.3")
    assert code == 3 and "matches 2 times" in json.loads(out)["warning"] + err, (out, err)
    assert _git(repo, "tag", "-l") == ""


def test_a_missing_file_is_refused(repo):
    _trunk(repo)
    _git(repo, "rm", "-q", "package.json")
    _git(repo, "commit", "-qm", "chore: drop package.json")
    code, out, err = run_cli(repo, "--json", "version", "cut", "--version", "1.2.3")
    assert code == 3 and "package.json" in json.loads(out)["warning"] + err, (out, err)


def test_a_pattern_without_exactly_one_group_is_a_named_config_problem(repo):
    from ddflow.config import Config
    from ddflow.core import flow as F

    cfg = Config()
    cfg.flow.version_files = {"a.toml": r"^version = .*$", "b.toml": r"(a)(b)", "c.toml": "(["}
    bad = [p for p in F.problems(cfg) if "version_files" in p]
    assert len(bad) == 3, bad


def test_gitflow_cut_puts_the_bump_on_the_release_and_into_production(gitflow):
    repo = gitflow
    (repo / "pyproject.toml").write_text(PYPROJECT)
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "chore: adopt")
    _configure(repo, pyproject__toml=PY)
    _commit(repo, "a.py", "a\n", "feat: a")
    code, _out, err = run_cli(repo, "--json", "version", "cut", "--version", "1.0.0")
    assert code == 0, err
    assert 'version = "1.0.0"' in _git(repo, "show", "v1.0.0:pyproject.toml")
    assert 'version = "1.0.0"' in _git(repo, "show", "main:pyproject.toml")
    assert 'version = "1.0.0"' in _git(repo, "show", "develop:pyproject.toml"), "back-merged"


def test_gitflow_pr_cut_carries_the_bump_in_the_release_request(gitflow_pr):
    repo, _forge, remote = gitflow_pr
    _git(repo, "checkout", "-q", "develop")
    (repo / "pyproject.toml").write_text(PYPROJECT)
    _configure(repo, pyproject__toml=PY)
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "chore: adopt")
    _commit(repo, "a.py", "a\n", "feat: a")
    _git(repo, "push", "-q", "origin", "develop")
    code, _out, err = run_cli(repo, "--json", "version", "cut", "--version", "1.0.0")
    assert code == 0, err
    shown = subprocess.run(
        ["git", "--git-dir", str(remote), "show", "release/1.0.0:pyproject.toml"],
        capture_output=True,
        text=True,
    ).stdout
    assert 'version = "1.0.0"' in shown, "the request must carry the bump"
    assert _state(repo).pending_releases, "the release request was not opened"


def test_a_trunk_cut_in_pr_mode_is_refused_because_the_bump_has_no_request(pr_repo):
    repo, _forge, _remote = pr_repo
    (repo / "pyproject.toml").write_text(PYPROJECT)
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "chore: adopt")
    _configure(repo, pyproject__toml=PY)
    _commit(repo, "a.py", "a\n", "feat: a")
    code, out, err = run_cli(repo, "--json", "version", "cut", "--version", "1.0.0")
    assert code == 3 and "gitflow" in json.loads(out)["warning"] + err, (out, err)
    assert _git(repo, "tag", "-l") == ""


def test_without_the_knob_nothing_is_touched(repo):
    run_cli(repo, "init")
    (repo / "pyproject.toml").write_text(PYPROJECT)
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "chore: adopt")
    _commit(repo, "a.py", "a\n", "feat: a")
    code, _out, err = run_cli(repo, "--json", "version", "cut", "--version", "1.0.0")
    assert code == 0, err
    assert 'version = "0.0.1"' in _git(repo, "show", "v1.0.0:pyproject.toml")
