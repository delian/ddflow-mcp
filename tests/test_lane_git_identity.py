"""A lane's private HOME hides the git identity: it is handed over as values (B6a483bb0f5)."""

from __future__ import annotations

import subprocess
import sys
import time

import pytest

from ddflow.infra import git as G
from ddflow.services import lane_driver as ld
from ddflow.services import lane_headless as lh

NAME, EMAIL = "Ada Lane", "ada@example.invalid"
COMMIT = ("git", "commit", "--allow-empty", "-q", "-m", "lane work")


def git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=False
    )


@pytest.fixture
def launcher(tmp_path, monkeypatch):
    """A launcher whose only git config is the file this returns, empty until a test fills it."""
    home = tmp_path / "launcher-home"
    home.mkdir()
    config = home / ".gitconfig"
    config.write_text("")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / "xdg"))
    monkeypatch.delenv("GIT_DIR", raising=False)
    return config


@pytest.fixture
def repo(tmp_path, launcher):
    r = tmp_path / "work"
    r.mkdir()
    assert git(r, "init", "-q", ".").returncode == 0
    return r


def identify(repo, *, name=NAME, email=EMAIL):
    if name:
        git(repo, "config", "user.name", name)
    if email:
        git(repo, "config", "user.email", email)


def run_lane(argv, cwd, **kw):
    """Start a lane, wait for it to end, and return its status."""
    driver = lh.ProcessHeadless()
    h = driver.start(ld.LaunchSpec(name="t", argv=tuple(argv), cwd=cwd, pass_env=("PATH",), **kw))
    assert not isinstance(h, ld.Result), h
    list(driver.events(h))
    deadline = time.time() + 30
    while driver.status(h).state == "running" and time.time() < deadline:
        time.sleep(0.02)
    st = driver.status(h)
    driver.stop(h)
    return st


def head_people(repo):
    return git(repo, "log", "-1", "--format=%an <%ae>|%cn <%ce>").stdout.strip()


def test_a_lane_commits_in_its_repo_as_the_repo_identity(repo):
    identify(repo)
    st = run_lane(COMMIT, str(repo))
    assert st.exit_code == 0
    assert head_people(repo) == f"{NAME} <{EMAIL}>|{NAME} <{EMAIL}>"


def test_an_identity_the_launcher_inherits_reaches_the_lane_as_values_only(repo, launcher):
    """The usual host: nothing in the repo's own config, the identity in the operator's global
    file. The lane gets the two values and still cannot read that file."""
    launcher.write_text(f"[user]\n\tname = {NAME}\n\temail = {EMAIL}\n\tsigningkey = SECRETKEY\n")
    assert run_lane(COMMIT, str(repo)).exit_code == 0
    assert head_people(repo) == f"{NAME} <{EMAIL}>|{NAME} <{EMAIL}>"
    probe = (
        "git",
        "config",
        "--global",
        "--get",
        "user.signingkey",
    )
    assert run_lane(probe, str(repo)).exit_code == 1


def test_the_lane_home_keeps_no_git_config(repo, tmp_path):
    identify(repo)
    home = tmp_path / "lane-home"
    assert run_lane(COMMIT, str(repo), home=home).exit_code == 0
    assert not (home / ".gitconfig").exists()
    assert not (home / ".config" / "git").exists()


@pytest.mark.parametrize(
    ("given", "named", "not_named"),
    [
        ({"name": "", "email": ""}, ("user.name", "user.email"), ()),
        ({"email": ""}, ("user.email",), ("user.name",)),
        ({"name": ""}, ("user.name",), ("user.email",)),
    ],
)
def test_a_repo_without_an_identity_refuses_the_launch(repo, tmp_path, given, named, not_named):
    identify(repo, **given)
    ran = tmp_path / "ran"
    r = lh.ProcessHeadless().start(
        ld.LaunchSpec(name="t", argv=("touch", str(ran)), cwd=str(repo), pass_env=("PATH",))
    )
    assert isinstance(r, ld.Result) and r.status == "failed"
    assert all(k in r.detail for k in named)
    assert not any(k in r.detail for k in not_named)
    assert str(repo) in r.detail
    assert not ran.exists()  # nothing started


def test_gits_email_fallback_is_not_an_identity(repo, monkeypatch):
    """git would accept $EMAIL for a missing user.email; the launch still asks for the real one."""
    identify(repo, email="")
    monkeypatch.setenv("EMAIL", EMAIL)
    r = lh.ProcessHeadless().start(
        ld.LaunchSpec(name="t", argv=("true",), cwd=str(repo), pass_env=("PATH", "EMAIL"))
    )
    assert isinstance(r, ld.Result) and r.status == "failed"
    assert "user.email" in r.detail


def test_a_refused_launch_leaves_no_private_home_behind(repo, tmp_path, monkeypatch):
    made = tmp_path / "made"
    monkeypatch.setattr(lh.fsio, "unused_dir", lambda prefix: made)
    r = lh.ProcessHeadless().start(ld.LaunchSpec(name="t", argv=("true",), cwd=str(repo)))
    assert isinstance(r, ld.Result) and r.status == "failed"
    assert not made.exists()


def test_a_placeholder_is_never_invented(repo, tmp_path):
    env = {}
    assert isinstance(lh._identity(str(repo), env), ld.Result)
    assert env == {}


def test_a_spec_that_states_the_identity_is_not_overruled(repo):
    identify(repo)
    fixed = {
        "GIT_AUTHOR_NAME": "Spec Author",
        "GIT_AUTHOR_EMAIL": "author@example.invalid",
        "GIT_COMMITTER_NAME": "Spec Committer",
        "GIT_COMMITTER_EMAIL": "committer@example.invalid",
    }
    assert run_lane(COMMIT, str(repo), fixed_env=fixed).exit_code == 0
    assert head_people(repo) == (
        "Spec Author <author@example.invalid>|Spec Committer <committer@example.invalid>"
    )


def test_a_spec_that_states_the_identity_launches_from_a_repo_with_none(repo):
    fixed = {
        "GIT_AUTHOR_NAME": "A",
        "GIT_AUTHOR_EMAIL": "a@example.invalid",
        "GIT_COMMITTER_NAME": "C",
        "GIT_COMMITTER_EMAIL": "c@example.invalid",
    }
    assert run_lane(COMMIT, str(repo), fixed_env=fixed).exit_code == 0


def test_a_spec_that_states_half_the_identity_gets_the_other_half_from_the_repo(repo):
    identify(repo)
    fixed = {"GIT_AUTHOR_NAME": "Spec Author"}
    assert run_lane(COMMIT, str(repo), fixed_env=fixed).exit_code == 0
    assert head_people(repo) == f"Spec Author <{EMAIL}>|{NAME} <{EMAIL}>"


def env_of(cwd, tmp_path):
    """The GIT_ variables the lane was given, as the lane itself sees them."""
    out = tmp_path / "env.txt"
    code = (
        "import os, sys; "
        "open(sys.argv[1], 'w').write(' '.join(sorted(k for k in os.environ if k[:4] == 'GIT_')))"
    )
    assert run_lane((sys.executable, "-c", code, str(out)), cwd).exit_code == 0
    return out.read_text()


def test_a_lane_outside_any_repo_needs_no_identity(launcher, tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert env_of(str(plain), tmp_path) == ""


def test_a_lane_with_no_working_directory_needs_no_identity(launcher, tmp_path):
    assert env_of(None, tmp_path) == ""


@pytest.mark.parametrize("failing", ["rev-parse", "config"])
def test_a_git_that_cannot_answer_refuses_rather_than_launching_unidentified(
    repo, monkeypatch, failing
):
    identify(repo)
    real = G.run

    def fake(where, *args, **kw):
        if args[0] == failing:
            return G.GitResult(2, "", "timed out", unavailable=True, timed_out=True)
        return real(where, *args, **kw)

    monkeypatch.setattr(lh.G, "run", fake)
    r = lh.ProcessHeadless().start(
        ld.LaunchSpec(name="t", argv=("true",), cwd=str(repo), pass_env=("PATH",))
    )
    assert isinstance(r, ld.Result) and r.status == "failed"
    assert "timed out" in r.detail
