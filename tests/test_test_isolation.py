"""The suite cannot reach the real repository (B1d82c6b76f).

A suite run from a git hook inherits ``GIT_DIR``; git then ignores ``-C``, so a fixture's
``git init --bare`` / ``git config user.name Test`` / ``git commit`` acted on the repository
that ran the gate (stray identity, ``core.bare = true``, "init" commits, a staged README.md).
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

import _isolation as ISO

from ddflow.config import Config
from ddflow.services.gateenv import REPO_VARS, child_env

TESTS = Path(__file__).resolve().parent


def _git(*args: str, cwd: Path) -> str:
    env = {k: v for k, v in os.environ.items() if k not in ISO.REPO_VARS}
    return subprocess.run(
        ["git", *args], cwd=cwd, env=env, capture_output=True, text=True, check=True
    ).stdout


#: What the `repo` fixture and the bare-remote fixtures do, run as a child whose
#: environment is a git hook's: GIT_DIR names a repository that is not the one being set up.
_PROBE = """
import sys
sys.path.insert(0, {tests!r})
import _isolation  # noqa: F401  (strips GIT_DIR, isolates HOME and the global config)
import subprocess
from pathlib import Path
w = Path({work!r})
for argv in (
    ["git", "init", "-q", "-b", "main", str(w / "proj")],
    ["git", "init", "-q", "--bare", "-b", "main", str(w / "remote.git")],
    ["git", "-C", str(w / "proj"), "config", "user.name", "Stray"],
    ["git", "-C", str(w / "proj"), "config", "user.email", "stray@example.com"],
):
    subprocess.run(argv, check=True)
(w / "proj" / "README.md").write_text("# proj\\n")
subprocess.run(["git", "-C", str(w / "proj"), "add", "README.md"], check=True)
subprocess.run(["git", "-C", str(w / "proj"), "commit", "-qm", "init"], check=True)
"""


def test_fixture_git_calls_never_reach_the_repo_a_hook_names_in_git_dir(tmp_path: Path) -> None:
    victim = tmp_path / "victim"
    victim.mkdir()
    _git("init", "-q", "-b", "main", ".", cwd=victim)
    _git("commit", "-q", "--allow-empty", "-m", "base", cwd=victim)
    work = tmp_path / "work"
    work.mkdir()
    real_before = ISO.snapshot(status=False)
    before = _git("config", "--local", "--list", cwd=victim)
    log_before = _git("log", "--format=%H %an", cwd=victim)

    env = {k: v for k, v in os.environ.items() if k not in (ISO.HOME_ENV, ISO.BASELINE_ENV)}
    env["GIT_DIR"] = str(victim / ".git")
    env["GIT_INDEX_FILE"] = str(victim / ".git" / "index")
    code = textwrap.dedent(_PROBE).format(tests=str(TESTS), work=str(work))
    p = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True)
    assert p.returncode == 0, p.stdout + p.stderr

    assert (work / "proj" / ".git").is_dir(), "the probe did not set up its own repository"
    assert _git("log", "--format=%an %s", cwd=work / "proj").strip() == "Stray init"
    assert _git("config", "--local", "--list", cwd=victim) == before
    assert _git("log", "--format=%H %an", cwd=victim) == log_before
    assert _git("status", "--porcelain", cwd=victim) == ""
    assert ISO.snapshot(status=False) == real_before


def test_the_suite_runs_with_git_isolated_from_the_developers_configuration() -> None:
    for var in ISO.REPO_VARS:
        assert var not in os.environ, var
    home = Path(os.environ[ISO.HOME_ENV])
    assert Path(os.environ["HOME"]) == home
    assert os.environ["XDG_CONFIG_HOME"].startswith(str(home))
    assert os.environ["GIT_CONFIG_GLOBAL"] == str(home / "gitconfig")
    assert os.environ["GIT_CONFIG_SYSTEM"] == os.devnull


def test_isolate_environment_strips_repo_vars_from_a_mapping(tmp_path: Path) -> None:
    env = {"GIT_DIR": "/x", "GIT_WORK_TREE": "/y", "KEEP": "1", "HOME": "/home/me"}
    home = ISO.isolate_environment(env)
    assert "GIT_DIR" not in env and "GIT_WORK_TREE" not in env
    assert env["KEEP"] == "1" and env["HOME"] == str(home) != "/home/me"
    assert env["GIT_CONFIG_GLOBAL"] == str(home / "gitconfig")


def test_the_guard_names_what_changed_in_the_real_repo_config() -> None:
    before = {"config": ["core.bare=false"], "bare": ["core.bare=false"], "status": []}
    after = {
        "config": ["core.bare=true", "user.name=Test"],
        "bare": ["core.bare=true"],
        "status": [" M README.md"],
    }
    said = "; ".join(ISO._changes(before, after))
    assert "user.name=Test" in said and "core.bare=true" in said and "README.md" in said
    assert ISO._changes(before, before) == []


def test_the_guard_fails_the_session_when_the_real_repo_changed(monkeypatch) -> None:
    monkeypatch.setattr(ISO, "_BASELINE", {"config": [], "bare": [], "status": []})
    monkeypatch.setattr(
        ISO, "snapshot", lambda status=True: {"config": ["user.name=Test"], "bare": [], "status": []}
    )
    session = SimpleNamespace(exitstatus=0)
    ISO.pytest_sessionfinish(session, 0)
    assert session.exitstatus == 1


def test_a_gate_command_never_inherits_the_callers_git_repository_variables() -> None:
    base = {"GIT_DIR": "/real/.git", "GIT_INDEX_FILE": "/real/.git/index", "PATH": "/bin"}
    assert child_env(Config(), base) == {"PATH": "/bin"}
    assert child_env(Config(), base, ci=True) == {"PATH": "/bin"}
    kept = Config()
    kept.gates.keep_env = ["GIT_DIR"]
    assert child_env(kept, base) == {"GIT_DIR": "/real/.git", "PATH": "/bin"}
    assert set(REPO_VARS) <= set(ISO.REPO_VARS)
