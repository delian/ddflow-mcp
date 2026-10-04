"""Onboarding stage 1: the harness wiring, pinned to the failure each piece prevents.

A project `.mcp.json` server Claude Code never starts, a reviewer endpoint either
committed to git or invisible inside every worktree, a shell whose `ddflow` is a different
copy of the code than the MCP server's -- all three fail silently: nothing errors, the
workflow simply behaves as if a feature were absent.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ddflow.services import harness as H
from ddflow.services.adopt import Refused
from ddflow.services.claudehooks import SettingsError

PIN = "/opt/ddflow-checkout"
ENTRY = {
    "command": "/opt/ddflow-checkout/.venv/bin/python",
    "args": ["-m", "ddflow.surfaces.mcp"],
    "env": {"PYTHONPATH": PIN},
}
MCP = {"mcpServers": {"ddflow": ENTRY}}


def _write(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def _settings(repo: Path) -> dict:
    return json.loads((repo / H.SETTINGS_REL).read_text())


# --- enabledMcpjsonServers -------------------------------------------------------------


def test_the_registered_servers_are_approved(repo):
    _write(repo / H.MCP_REL, MCP)
    actions = H.enable_project_servers(repo)
    assert _settings(repo)[H.ENABLED_KEY] == ["ddflow"]
    assert not [a for a in actions if isinstance(a, Refused)]
    assert any("approved ddflow" in a for a in actions)


def test_every_registered_server_is_approved_not_only_ddflow(repo):
    _write(repo / H.MCP_REL, {"mcpServers": {"ddflow": ENTRY, "other": {"command": "x"}}})
    H.enable_project_servers(repo)
    assert _settings(repo)[H.ENABLED_KEY] == ["ddflow", "other"]


def test_the_operators_settings_and_approvals_survive_and_install_is_idempotent(repo):
    _write(
        repo / H.SETTINGS_REL,
        {"permissions": {"defaultMode": "plan"}, H.ENABLED_KEY: ["other"], H.DISABLED_KEY: ["old"]},
    )
    _write(repo / H.MCP_REL, MCP)
    H.enable_project_servers(repo)
    first = _settings(repo)
    assert first[H.ENABLED_KEY] == ["other", "ddflow"]
    assert first["permissions"] == {"defaultMode": "plan"}
    assert first[H.DISABLED_KEY] == ["old"]
    actions = H.enable_project_servers(repo)
    assert _settings(repo) == first
    assert any("already approved" in a for a in actions)


def test_a_disabled_server_is_reported_not_silently_re_enabled(repo):
    """`disabledMcpjsonServers` rejects the server in every permission mode, so clearing
    it here would reverse a decision this function has no mandate to touch."""
    _write(repo / H.SETTINGS_REL, {H.DISABLED_KEY: ["ddflow"]})
    _write(repo / H.MCP_REL, MCP)
    actions = H.enable_project_servers(repo)
    denied = [a for a in actions if isinstance(a, Refused)]
    assert len(denied) == 1 and "disabledMcpjsonServers" in denied[0]
    assert H.ENABLED_KEY not in _settings(repo)
    assert _settings(repo)[H.DISABLED_KEY] == ["ddflow"]


def test_an_unreadable_settings_file_is_left_alone(repo):
    path = repo / H.SETTINGS_REL
    path.parent.mkdir(parents=True)
    path.write_text("{ not json")
    _write(repo / H.MCP_REL, MCP)
    with pytest.raises(SettingsError):
        H.enable_project_servers(repo)
    assert path.read_text() == "{ not json"


def test_a_non_list_approval_key_refuses_rather_than_overwrites(repo):
    _write(repo / H.SETTINGS_REL, {H.ENABLED_KEY: "ddflow"})
    _write(repo / H.MCP_REL, MCP)
    with pytest.raises(SettingsError):
        H.enable_project_servers(repo)


@pytest.mark.parametrize("bad", [{}, "", 0, False, None])
def test_a_falsy_non_list_key_refuses_rather_than_overwrites(repo, bad):
    """`get(k) or []` turned `{}`/`""`/`false` into a list and rewrote it (rubber_duck)."""
    _write(repo / H.SETTINGS_REL, {H.ENABLED_KEY: bad})
    _write(repo / H.MCP_REL, MCP)
    with pytest.raises(SettingsError):
        H.enable_project_servers(repo)
    assert _settings(repo)[H.ENABLED_KEY] == bad


def test_no_registered_servers_is_a_no_op(repo):
    assert "nothing to approve" in H.enable_project_servers(repo)[0]
    assert not (repo / H.SETTINGS_REL).exists(), "a settings file was created for no server"


def test_an_unreadable_mcp_file_refuses_rather_than_approves_nothing(repo):
    (repo / H.MCP_REL).write_text("{ nope")
    with pytest.raises(H.HarnessError):
        H.project_servers(repo)


def test_an_mcp_file_that_is_not_an_object_refuses(repo):
    (repo / H.MCP_REL).write_text("[]")
    with pytest.raises(H.HarnessError):
        H.project_servers(repo)


# --- machine-local config from a sibling ----------------------------------------------


def _sibling(tmp_path: Path, files: dict[str, str]) -> Path:
    source = tmp_path / "sibling"
    for rel, text in files.items():
        (source / rel).parent.mkdir(parents=True, exist_ok=True)
        (source / rel).write_text(text)
    return source


def test_a_siblings_machine_local_config_is_copied_and_git_ignored(repo, tmp_path):
    source = _sibling(
        tmp_path,
        {
            H.LOCAL_CONFIGS[0]: "[[reviewer]]\nname = 'local'\n",
            H.LOCAL_CONFIGS[1]: '[gate.unit_tests]\ncommand = "pytest -q -n 8"\n',
        },
    )
    actions = H.copy_local_configs(repo, source)
    assert (repo / H.LOCAL_CONFIGS[0]).read_text() == "[[reviewer]]\nname = 'local'\n"
    assert (repo / H.LOCAL_CONFIGS[1]).read_text().startswith("[gate.unit_tests]")
    assert H.git_ignored(repo, H.LOCAL_CONFIGS[0]) is True
    assert any("copied" in a for a in actions)


def test_the_copy_never_overwrites_a_local_file_already_here(repo, tmp_path):
    source = _sibling(tmp_path, {H.LOCAL_CONFIGS[0]: "theirs\n"})
    (repo / H.LOCAL_CONFIGS[0]).parent.mkdir(parents=True)
    (repo / H.LOCAL_CONFIGS[0]).write_text("ours\n")
    actions = H.copy_local_configs(repo, source)
    assert (repo / H.LOCAL_CONFIGS[0]).read_text() == "ours\n"
    assert any("kept" in a for a in actions)


def test_a_missing_sibling_file_is_reported_not_created(repo, tmp_path):
    source = _sibling(tmp_path, {H.LOCAL_CONFIGS[1]: "x\n"})
    actions = H.copy_local_configs(repo, source)
    assert not (repo / H.LOCAL_CONFIGS[0]).exists()
    assert any("no .ddflow/local/reviewers.toml" in a for a in actions)


def test_a_dangling_symlink_is_kept_not_written_through(repo, tmp_path):
    """`exists()` is False for a dangling link, so copy2 would follow it and write the
    machine's endpoints outside the git-ignored directory (rubber_duck on 91c639e)."""
    source = _sibling(tmp_path, {H.LOCAL_CONFIGS[0]: "theirs\n"})
    link = repo / H.LOCAL_CONFIGS[0]
    link.parent.mkdir(parents=True)
    outside = tmp_path / "outside.toml"
    link.symlink_to(outside)
    actions = H.copy_local_configs(repo, source)
    assert not outside.exists(), "the copy wrote through a dangling symlink"
    assert any("kept" in a for a in actions)


def test_copied_local_config_is_flagged_when_worktrees_would_not_get_it(repo, tmp_path):
    """A git-ignored file does not reach a worktree; `[worktree].local_files` is how a
    claim copies it in, and onboarding has to say so or the gate reads the wrong config."""
    source = _sibling(tmp_path, {H.LOCAL_CONFIGS[0]: "x\n"})
    actions = H.copy_local_configs(repo, source)
    assert any("worktree" in a and "local_files" in a for a in actions)


def test_the_worktree_note_is_silent_once_local_files_lists_them(repo, tmp_path):
    (repo / ".ddflow").mkdir()
    (repo / ".ddflow" / "config.toml").write_text(
        "[worktree]\nlocal_files = " + json.dumps(list(H.LOCAL_CONFIGS)) + "\n"
    )
    source = _sibling(tmp_path, {H.LOCAL_CONFIGS[0]: "x\n"})
    actions = H.copy_local_configs(repo, source)
    assert not any("local_files" in a for a in actions)


def test_an_unreadable_config_is_not_reported_as_not_listed(repo, tmp_path, monkeypatch):
    """'could not read the config' must not be rendered as 'these are not listed'
    (roborev on e9bbfd74)."""
    source = _sibling(tmp_path, {H.LOCAL_CONFIGS[0]: "x\n"})
    monkeypatch.setattr(H, "_local_files", lambda repo: None)
    actions = H.copy_local_configs(repo, source)
    assert any("could not read [worktree].local_files" in a for a in actions)
    assert not any("add them to" in a for a in actions)


def test_without_git_the_copy_still_happens_with_a_caveat(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    source = _sibling(tmp_path, {H.LOCAL_CONFIGS[0]: "x\n"})
    actions = H.copy_local_configs(target, source)
    assert (target / H.LOCAL_CONFIGS[0]).read_text() == "x\n"
    assert any("could not say" in a for a in actions)


def test_git_ignored_answers_false_for_a_tracked_path_and_none_without_git(repo, tmp_path):
    assert H.git_ignored(repo, "README.md") is False
    plain = tmp_path / "plain"
    plain.mkdir()
    assert H.git_ignored(plain, "x") is None


# --- the shell command ----------------------------------------------------------------


def test_the_wrapper_mirrors_the_pin_and_runs_the_cli_not_the_mcp_module():
    text = H.wrapper_text(ENTRY)
    assert text.startswith("#!/bin/sh\n") and H.WRAPPER_MARK in text
    assert f"PYTHONPATH={PIN}${{PYTHONPATH:+:$PYTHONPATH}}" in text
    assert "exec /opt/ddflow-checkout/.venv/bin/python -m ddflow" in text
    assert "ddflow.surfaces.mcp" not in text, "the CLI entry, not the MCP module"
    assert text.endswith('"$@"\n')


def test_a_relative_entry_is_resolved_against_the_project_root(tmp_path):
    """The client starts a project-scoped entry in the project; the wrapper runs from
    wherever the shell is -- so a relative pin/command must be made absolute
    (rubber_duck on 91c639e)."""
    repo = tmp_path / "proj"
    (repo / ".venv" / "bin").mkdir(parents=True)
    entry = {
        "command": ".venv/bin/python",
        "args": ["-m", "ddflow.surfaces.mcp"],
        "env": {"PYTHONPATH": "src"},
    }
    text = H.wrapper_text(entry, base=repo)
    assert f"exec {repo}/.venv/bin/python -m ddflow" in text
    assert f"PYTHONPATH={repo}/src" in text
    bindir = tmp_path / "bin"
    refused = H.install_shell_command(entry, bindir=bindir, path="")
    assert isinstance(refused, Refused) and "relative" in refused
    assert not bindir.exists()


def test_the_entrys_argv_prefix_is_preserved_not_guessed(tmp_path):
    """`uv run python -m ddflow.mcp` mirrors as `uv run python -m ddflow`, never the
    broken `uv -m ddflow` an interpreter-only assumption produced (rubber_duck)."""
    uv = {
        "command": "uv",
        "args": ["run", "python", "-m", "ddflow.surfaces.mcp"],
        "env": {"PYTHONPATH": "/opt/pin"},
    }
    assert "exec uv run python -m ddflow" in H.wrapper_text(uv)
    no_module = {"command": "ddflow-mcp", "env": {"PYTHONPATH": "/opt/pin"}}
    refused = H.install_shell_command(no_module, bindir=tmp_path / "bin", path="")
    assert isinstance(refused, Refused) and "no `-m" in refused


def test_without_a_bindir_the_wrapper_is_proposed_not_written(tmp_path, monkeypatch):
    """A library must not install a machine-wide command on a default (critic)."""
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    action = H.install_shell_command(ENTRY, path="")
    assert "not installed" in action and "-m ddflow" in action
    assert not (home / ".local" / "bin" / "ddflow").exists()


def test_a_local_dir_git_does_not_ignore_refuses_before_any_copy(repo, tmp_path):
    """The refusal contract is 'nothing was written'; the check runs before the dir is
    ensured, so a project that un-ignores local/ gets a refusal, not a half-done copy."""
    local = repo / ".ddflow" / "local"
    local.mkdir(parents=True)
    (local / ".gitignore").write_text("!*\n")
    source = _sibling(tmp_path, {H.LOCAL_CONFIGS[0]: "theirs\n"})
    actions = H.copy_local_configs(repo, source)
    assert isinstance(actions[0], Refused) and "NOT git-ignored" in actions[0]
    assert not (repo / H.LOCAL_CONFIGS[0]).exists()


def test_a_failed_refresh_is_reported_not_raised(tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    H.install_shell_command(ENTRY, bindir=bindir, path="")
    moved = {**ENTRY, "env": {"PYTHONPATH": "/opt/moved"}}

    def boom(path, text):
        raise OSError("disk full")

    monkeypatch.setattr(H, "_write_executable", boom)
    action = H.install_shell_command(moved, bindir=bindir, path="")
    assert isinstance(action, Refused) and "could not update" in action


def test_our_wrapper_reached_through_a_symlink_is_refused(tmp_path):
    """Reading through a link found the marker; writing would follow it and rewrite
    whatever it points at (roborev on e9bbfd74)."""
    real = tmp_path / "elsewhere"
    real.write_text(H.wrapper_text(ENTRY))
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "ddflow").symlink_to(real)
    moved = {**ENTRY, "env": {"PYTHONPATH": "/opt/moved"}}
    action = H.install_shell_command(moved, bindir=bindir, path="")
    assert isinstance(action, Refused) and "symlink" in action
    assert real.read_text() == H.wrapper_text(ENTRY), "the linked file was rewritten"
    assert (bindir / "ddflow").is_symlink()


def test_an_env_that_is_not_an_object_is_refused_not_crashed(tmp_path):
    broken = {"command": "python", "args": ["-m", "x"], "env": ["PYTHONPATH=/x"]}
    action = H.install_shell_command(broken, bindir=tmp_path / "bin", path="")
    assert isinstance(action, Refused) and "env" in action


def test_a_tilde_path_expands_against_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    entry = {
        "command": "~/venv/bin/python",
        "args": ["-m", "ddflow.surfaces.mcp"],
        "env": {"PYTHONPATH": "~/src"},
    }
    text = H.wrapper_text(entry)
    assert f"exec {tmp_path}/venv/bin/python -m ddflow" in text
    assert f"PYTHONPATH={tmp_path}/src" in text


def test_the_wrapper_runs_the_pinned_checkout_not_an_ambient_copy(tmp_path):
    """The probe behind R-onboard-harness-pin, as a test: a decoy ddflow is importable
    from the inherited PYTHONPATH, and the wrapper still runs the pinned one."""
    pinned = tmp_path / "pinned" / "ddflow"
    pinned.mkdir(parents=True)
    (pinned / "__init__.py").write_text("")
    (pinned / "__main__.py").write_text(
        "import ddflow, sys\nprint('PINNED', ddflow.__file__, sys.argv[1:])\n"
    )
    decoy = tmp_path / "decoy" / "ddflow"
    decoy.mkdir(parents=True)
    (decoy / "__init__.py").write_text("")
    (decoy / "__main__.py").write_text("print('DECOY')\n")
    entry = {
        "command": sys.executable,
        "args": ["-m", "ddflow.surfaces.mcp"],
        "env": {"PYTHONPATH": str(tmp_path / "pinned")},
    }
    bindir = tmp_path / "bin"
    action = H.install_shell_command(entry, bindir=bindir, path="")
    target = bindir / "ddflow"
    assert target.is_file() and os.access(target, os.X_OK)
    proc = subprocess.run(
        [str(target), "status"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(tmp_path / "decoy")},
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.startswith("PINNED")
    assert "decoy" not in proc.stdout
    assert "add it to PATH" in action


def test_an_unpinned_entry_has_no_wrapper_and_nothing_is_written(tmp_path):
    uvx = {"command": "uvx", "args": ["ddflow-mcp"]}
    assert H.pinned_cli(uvx) is None
    with pytest.raises(ValueError):
        H.wrapper_text(uvx)
    bindir = tmp_path / "bin"
    assert "not PYTHONPATH-pinned" in H.install_shell_command(uvx, bindir=bindir)
    assert not bindir.exists()
    assert H.pinned_cli({"command": "python"}) is None, "no env at all"
    assert H.pinned_cli("not a dict") is None


def test_the_operators_own_ddflow_is_never_overwritten(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    target = bindir / "ddflow"
    target.write_text("#!/bin/sh\necho mine\n")
    action = H.install_shell_command(ENTRY, bindir=bindir, path="")
    assert isinstance(action, Refused) and "not ddflow's wrapper" in action
    assert target.read_text() == "#!/bin/sh\necho mine\n"


def test_installing_twice_is_safe_and_a_new_pin_refreshes_the_wrapper(tmp_path):
    bindir = tmp_path / "bin"
    H.install_shell_command(ENTRY, bindir=bindir, path="")
    assert "already" in H.install_shell_command(ENTRY, bindir=bindir, path="")
    moved = {**ENTRY, "env": {"PYTHONPATH": "/opt/moved"}}
    assert "updated" in H.install_shell_command(moved, bindir=bindir, path="")
    assert "/opt/moved" in (bindir / "ddflow").read_text()


def test_on_path_matches_by_absolute_directory(tmp_path):
    assert H.on_path(tmp_path, path=f"/usr/bin{os.pathsep}{tmp_path}")
    assert not H.on_path(tmp_path, path="/usr/bin")
    assert not H.on_path(tmp_path, path="")
