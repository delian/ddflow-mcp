"""The activation scripts ddflow serves for bash, zsh and fish."""

from __future__ import annotations

import os
import shutil
import subprocess

import pytest

from ddflow.services.completion import scripts


@pytest.mark.parametrize("shell", ["bash", "zsh", "fish"])
def test_script_is_served_verbatim_and_bounded(shell):
    text = scripts.for_shell(shell)
    assert text  # non-empty
    assert len(text.encode()) < 20 * 1024


def test_for_shell_refuses_an_unknown_shell():
    with pytest.raises(ValueError):
        scripts.for_shell("powershell")


@pytest.mark.parametrize(
    ("shell", "syntax"),
    [("bash", "bash -n"), ("zsh", "zsh -n"), ("fish", "fish -n")],
)
def test_script_parses_in_its_shell(shell, syntax):
    if shutil.which(shell) is None:
        pytest.skip(f"{shell} not installed")
    probe = subprocess.run(
        syntax, shell=True, input=scripts.for_shell(shell), text=True, capture_output=True
    )
    assert probe.returncode == 0, probe.stderr


def test_script_without_argcomplete_returns_0_with_a_message(tmp_path):
    """Without `register-python-argcomplete` on PATH the script prints a message, returns 0."""
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash not installed")
    empty = tmp_path / "empty"
    empty.mkdir()
    env = dict(os.environ, PATH=str(empty))
    probe = subprocess.run(
        [bash],
        input=scripts.for_shell("bash"),
        text=True,
        capture_output=True,
        env=env,
    )
    assert probe.returncode == 0
    assert "argcomplete" in probe.stderr
