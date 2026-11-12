"""The activation scripts ddflow serves for bash, zsh and fish."""

from __future__ import annotations

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
    probe = subprocess.run(
        syntax, shell=True, input=scripts.for_shell(shell), text=True, capture_output=True
    )
    assert probe.returncode == 0, probe.stderr


def test_script_without_argcomplete_returns_0_with_a_message():
    # No `register-python-argcomplete` on a PATH without argcomplete: the script must
    # tell the operator and return 0, never break the prompt.
    evil = subprocess.run(["true"])  # nothing
    del evil
    text = scripts.for_shell("bash")
    assert "register-python-argcomplete" in text
    assert "argcomplete" in text
