"""A hook or launcher that records a ddflow that no longer exists (B-dangling-precommit-hook).

The installed git hook used to `exec` an absolute path baked in at install time. Delete
that venv and EVERY `git commit` failed with "not found", and neither `doctor` nor
`hooks status` said so. Now the hook resolves ddflow at run time -- the recorded launcher,
then `ddflow` on PATH -- and FAILS OPEN with one line when neither exists; and `doctor`
and `hooks status` name the dangling hook, Claude hook command and MCP entry and the
command that refreshes it. Everything runs in a throwaway project.
"""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.services import claudehooks as CH
from ddflow.services import enforce as E
from ddflow.services import launchers as LA

GONE = "/nonexistent-ddflow-venv/bin/ddflow"
_GIT_ENV = ("GIT_DIR", "GIT_INDEX_FILE", "GIT_WORK_TREE", "GIT_PREFIX")


def _commit(repo: Path, env_extra: dict | None = None) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k not in _GIT_ENV}
    env.update(env_extra or {})
    (repo / "f.txt").write_text(os.urandom(4).hex())
    subprocess.run(["git", "-C", str(repo), "add", "f.txt"], check=True, env=env)
    return subprocess.run(
        ["git", "-C", str(repo), "commit", "-qm", "x"],
        capture_output=True, text=True, env=env, timeout=120,
    )  # fmt: skip


def _hook(repo: Path, name: str = "pre-commit") -> Path:
    return E.hooks_dir(repo) / name


def _legacy_hook(repo: Path, name: str = "pre-commit") -> Path:
    """The hook as it was written BEFORE the fix: an exec of a baked-in absolute path."""
    h = _hook(repo, name)
    check = "check-commit" if name == "pre-commit" else "check-msg"
    h.write_text(f'#!/bin/sh\n{E.HOOK_MARKER}\nexec "{GONE}" hooks {check} "$@"\n')
    h.chmod(h.stat().st_mode | stat.S_IXUSR)
    return h


def _dead_launcher(repo: Path) -> None:
    """Install, then make what the hook recorded disappear."""
    assert E.install(repo).startswith("installed")
    for name in ("pre-commit", "commit-msg"):
        h = _hook(repo, name)
        text = h.read_text()
        for path in LA.recorded_paths(text):
            text = text.replace(path, "/nonexistent-ddflow-venv/" + path.lstrip("/"))
        h.write_text(text)


@pytest.fixture
def bare_path(tmp_path, monkeypatch):
    """A PATH with git and sh but no ddflow."""
    shim = tmp_path / "bin"
    shim.mkdir()
    for tool in ("git", "sh", "env", "python3"):
        for d in os.environ["PATH"].split(os.pathsep):
            if (Path(d) / tool).exists():
                (shim / tool).symlink_to(Path(d) / tool)
                break
    monkeypatch.setenv("PATH", str(shim))
    return shim


# --- the hook fails open -------------------------------------------------------------


def test_a_legacy_hook_with_a_dead_path_blocks_the_commit_today(repo):
    """The reported symptom, pinned on the OLD hook text: why the fix exists."""
    _legacy_hook(repo)
    r = _commit(repo)
    assert r.returncode != 0 and "not found" in r.stderr.lower()


def test_a_dead_recorded_launcher_does_not_fail_the_commit(repo, bare_path):
    _dead_launcher(repo)
    r = _commit(repo)
    assert r.returncode == 0, r.stderr
    assert r.stderr.count("ddflow: not found") >= 1, r.stderr
    assert "ddflow hooks install" in r.stderr


def test_the_skip_is_one_line_per_hook(repo, bare_path):
    _dead_launcher(repo)
    r = _commit(repo)
    assert len([ln for ln in r.stderr.splitlines() if "not found" in ln]) == 2, r.stderr


def test_a_dead_recorded_launcher_falls_back_to_ddflow_on_path(repo, tmp_path, bare_path):
    _dead_launcher(repo)
    marker = tmp_path / "ran"
    exe = bare_path / "ddflow"
    exe.write_text(f'#!/bin/sh\necho "$@" >> {marker}\nexit 0\n')
    exe.chmod(0o755)
    r = _commit(repo)
    assert r.returncode == 0, r.stderr
    assert "ddflow: not found" not in r.stderr
    ran = marker.read_text()
    assert "hooks check-commit" in ran and "hooks check-msg" in ran, ran


def test_the_path_ddflow_is_run_not_failed_open_even_when_it_refuses(repo, tmp_path, bare_path):
    """Fail open is for a MISSING tool only: a ddflow that runs and says no still says no."""
    _dead_launcher(repo)
    exe = bare_path / "ddflow"
    exe.write_text("#!/bin/sh\necho refused >&2\nexit 1\n")
    exe.chmod(0o755)
    assert _commit(repo).returncode != 0


def test_a_live_recorded_launcher_is_still_used(repo, tmp_path, monkeypatch):
    """The recorded launcher comes FIRST, so a source checkout runs its own code."""
    line = E.command_line("hooks check-commit", exec_=True, extra='"$@"')
    assert LA.recorded_paths(line)
    assert not LA.check_command("x", line, "fix")


# --- doctor and hooks status say so ----------------------------------------------------


def test_the_recorded_paths_are_read_back_from_both_hook_formats():
    new = E.command_line("hooks check-commit", exec_=True, extra='"$@"')
    assert LA.recorded_paths(new) and all(p.startswith("/") for p in LA.recorded_paths(new))
    assert LA.recorded_paths(f'exec "{GONE}" hooks check-commit "$@"') == [GONE]
    assert LA.recorded_paths('PYTHONPATH="/x/y${PYTHONPATH:+:$PYTHONPATH}" exec "/p/py" -m ddflow a') == [
        "/p/py",
        "/x/y/ddflow/__init__.py",
    ]  # fmt: skip


def _init(repo: Path) -> None:
    code, out, err = run_cli(repo, "init")
    assert code == 0, out + err


@pytest.mark.parametrize("legacy", [False, True], ids=["fail-open-hook", "legacy-hook"])
def test_hooks_status_names_a_dangling_git_hook(repo, bare_path, legacy):
    _init(repo)
    if legacy:
        E.install(repo)
        _legacy_hook(repo)
    else:
        _dead_launcher(repo)
    _code, out, err = run_cli(repo, "hooks", "status")
    text = out + err
    assert "DANGLING LAUNCHER" in text and "no longer exists" in text, text
    assert "ddflow hooks install" in text and "pre-commit" in text


def test_doctor_reports_a_dangling_git_hook_as_a_problem(repo, bare_path):
    _init(repo)
    _dead_launcher(repo)
    code, out, err = run_cli(repo, "doctor")
    text = out + err
    assert code == 1, text
    assert "no longer exists" in text and "NOTHING runs it" in text
    assert "ddflow hooks install" in text


def test_doctor_only_notes_a_dangling_hook_when_path_still_has_ddflow(repo, tmp_path, bare_path):
    _init(repo)
    _dead_launcher(repo)
    exe = bare_path / "ddflow"
    exe.write_text("#!/bin/sh\nexit 0\n")
    exe.chmod(0o755)
    findings = LA.findings(repo)
    assert findings and all(d.fallback for d in findings)
    assert "falls back to `ddflow` on PATH" in findings[0].render()


def test_a_healthy_project_reports_nothing(repo):
    _init(repo)
    assert E.install(repo).startswith("installed")
    assert LA.findings(repo) == []
    _code, out, err = run_cli(repo, "hooks", "status")
    assert "DANGLING" not in out + err


def test_hooks_install_refreshes_a_dangling_hook(repo, bare_path):
    _init(repo)
    _dead_launcher(repo)
    assert LA.findings(repo)
    code, out, err = run_cli(repo, "hooks", "install")
    assert code == 0, out + err
    assert LA.findings(repo) == []


def test_a_dangling_claude_hook_command_is_reported_and_refreshed(repo, bare_path):
    line = E.command_line(CH.MARKER)
    for p in LA.recorded_paths(line):
        line = line.replace(p, "/nonexistent-ddflow-venv" + p)
    CH.install(repo, line)
    found = LA.findings(repo)
    assert len(found) == 1 and ".claude/settings.json" in found[0].where
    assert "ddflow hooks install --claude" in found[0].render()
    code, out, err = run_cli(repo, "hooks", "install", "--claude")
    assert code == 0, out + err
    assert LA.findings(repo) == []


def test_a_claude_hook_command_fails_open_when_ddflow_is_gone(repo, bare_path):
    line = E.command_line(CH.MARKER)
    for p in LA.recorded_paths(line):
        line = line.replace(p, "/nonexistent-ddflow-venv" + p)
    r = subprocess.run(["sh", "-c", line], capture_output=True, text=True, cwd=repo)
    assert r.returncode == 0 and "ddflow: not found" in r.stderr


def test_a_dangling_mcp_entry_is_reported(repo):
    (repo / ".mcp.json").write_text(
        json.dumps(
            {"mcpServers": {"ddflow": {"command": "/nonexistent-ddflow-venv/bin/python",
                                       "args": ["-m", "ddflow.surfaces.mcp"],
                                       "env": {"PYTHONPATH": "/nonexistent-ddflow-src"}},
                            "other": {"command": "/nonexistent/other"}}}
        )
    )  # fmt: skip
    found = LA.findings(repo)
    assert len(found) == 1 and ".mcp.json" in found[0].where
    assert "ddflow adopt" in found[0].render()
    assert "/nonexistent-ddflow-venv/bin/python" in found[0].render()


def test_a_live_mcp_entry_is_not_reported(repo):
    (repo / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"ddflow": {"command": sys.executable, "args": []}}})
    )
    assert LA.findings(repo) == []


def test_doctor_reports_a_dangling_mcp_entry(repo):
    _init(repo)
    (repo / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"ddflow": {"command": "/nonexistent-ddflow-venv/bin/python"}}})
    )
    code, out, err = run_cli(repo, "doctor")
    assert code == 1 and re.search(r"\.mcp\.json.*no longer exists", out + err, re.S)


def test_the_gemini_flag_reaches_ddflow_in_every_branch():
    """The flag is an argument of ddflow, not of the `if` the fallback wraps it in."""
    line = E.command_line(CH.PROMPT_MARKER, extra="--gemini")
    assert line.count("hooks prompt --gemini") == 2, line
    assert not line.rstrip().endswith("fi --gemini")


def test_a_legacy_line_never_claims_a_path_fallback(repo, tmp_path, bare_path):
    """An older line execs its dead path; saying it falls back to PATH would be false."""
    exe = bare_path / "ddflow"
    exe.write_text("#!/bin/sh\nexit 0\n")
    exe.chmod(0o755)
    d = LA.check_command("x", f'exec "{GONE}" hooks check-commit "$@"', "ddflow hooks install")
    assert d and d.fallback is False and "NOTHING runs it" in d.render()


def test_one_good_pythonpath_entry_is_enough_for_an_mcp_entry(repo, tmp_path):
    pkg = tmp_path / "src" / "ddflow"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (repo / ".mcp.json").write_text(
        json.dumps(
            {"mcpServers": {"ddflow": {"command": sys.executable,
                                       "env": {"PYTHONPATH": f"/opt/none{os.pathsep}{tmp_path / 'src'}"}}}}
        )
    )  # fmt: skip
    assert LA.findings(repo) == []


def test_the_notice_names_the_refresh_that_fits_the_hook(repo, bare_path):
    line = E.command_line(CH.MARKER, refresh="ddflow hooks install --claude")
    for p in LA.recorded_paths(line):
        line = line.replace(p, "/nonexistent-ddflow-venv" + p)
    r = subprocess.run(["sh", "-c", line], capture_output=True, text=True, cwd=repo)
    assert "run: ddflow hooks install --claude" in r.stderr, r.stderr


def test_a_launcher_that_is_no_longer_executable_is_reported(repo, tmp_path):
    """The hook's probe is `[ -x ]`, so a script that lost its execute bit takes the
    fallback exactly as a deleted one does -- and must be reported the same way."""
    exe = tmp_path / "ddflow"
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    line = f'exec "{exe}" hooks check-commit "$@"'
    assert LA.check_command("x", line, "fix") is None
    exe.chmod(0o644)
    d = LA.check_command("x", line, "fix")
    assert d and d.missing == (str(exe),)
