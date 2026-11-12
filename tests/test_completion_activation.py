"""The activation output registers completion under every spelling a user types.

Defect (Bcomp-engine-never-answers): the output began with ``#`` comments and spanned many
lines, so the unquoted ``eval $(ddflow completion zsh)`` joined it into one comment and
nothing ran, silently. These tests run REAL shells over the real output, in the three
spellings, and assert the registration is there afterwards.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from ddflow.services import completion_shell as CS

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def shim(tmp_path):
    """A ``ddflow`` on PATH whose ``completion <shell>`` prints this tree's activation text."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    exe = bin_dir / "ddflow"
    exe.write_text(
        "#!/bin/sh\n"
        f'exec "{sys.executable}" -c \'import sys; sys.path.insert(0, "{ROOT}"); '
        "from ddflow.services import completion_shell as C; "
        'sys.stdout.write(C.for_shell(sys.argv[2]))\' "$@"\n'
    )
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    env = {k: v for k, v in os.environ.items() if k not in ("BASH_ENV", "ENV", "ZDOTDIR")}
    env.update(PATH=f"{bin_dir}:{os.environ['PATH']}", HOME=str(tmp_path))
    return env


def _run(shell, script, env):
    exe = shutil.which(shell)
    if exe is None:
        pytest.skip(f"{shell} not installed")
    args = [exe, "-fc", script] if shell == "zsh" else [exe, "-c", script]
    if shell == "fish":
        args = [exe, "--no-config", "-c", script]
    return subprocess.run(args, env=env, text=True, capture_output=True, timeout=60)


@pytest.mark.parametrize("shell", CS.SHELLS)
def test_output_is_one_comment_free_line(shell):
    text = CS.for_shell(shell)
    assert text.endswith("\n")
    assert text.count("\n") == 1
    assert not text.lstrip().startswith("#")


@pytest.mark.parametrize("shell", ["bash", "zsh"])
def test_output_has_no_whitespace_a_word_split_would_change(shell):
    body = CS.for_shell(shell)
    assert "  " not in body
    assert "\t" not in body


_ZSH_FORMS = {
    "quoted": 'eval "$(ddflow completion zsh)"',
    "unquoted": "eval $(ddflow completion zsh)",
    "source": "source <(ddflow completion zsh)",
}


@pytest.mark.parametrize("form", sorted(_ZSH_FORMS))
@pytest.mark.parametrize("compinit_first", [True, False])
def test_zsh_registers_under_every_spelling(shim, form, compinit_first):
    boot = "autoload -Uz compinit; compinit -u; " if compinit_first else ""
    probe = _run("zsh", f'{boot}{_ZSH_FORMS[form]}; print -r -- "REG=${{_comps[ddflow]}}"', shim)
    assert "REG=_python_argcomplete" in probe.stdout, probe.stdout + probe.stderr


@pytest.mark.parametrize("form", ["quoted", "unquoted", "source"])
def test_bash_registers_under_every_spelling(shim, form):
    spelling = {
        "quoted": 'eval "$(ddflow completion bash)"',
        "unquoted": "eval $(ddflow completion bash)",
        "source": "source <(ddflow completion bash)",
    }[form]
    probe = _run("bash", f"{spelling}; complete -p ddflow", shim)
    assert "_python_argcomplete" in probe.stdout, probe.stdout + probe.stderr


@pytest.mark.parametrize(
    "spelling",
    [
        "ddflow completion fish | source",
        "source (ddflow completion fish | psub)",
        "eval (ddflow completion fish)",
        "eval (ddflow completion fish | string collect)",
    ],
)
def test_fish_registers_under_every_spelling(shim, spelling):
    probe = _run("fish", f"{spelling}; complete -c ddflow", shim)
    assert "__fish_ddflow_complete" in probe.stdout, probe.stdout + probe.stderr


@pytest.mark.parametrize("shell", ["bash", "zsh", "fish"])
def test_without_argcomplete_line_runs_and_says_so(shell, monkeypatch):
    """The message line is valid in the real shell (no inner quote ends its string)."""
    monkeypatch.setitem(sys.modules, "argcomplete", None)
    probe = _run(shell, CS.for_shell(shell), dict(os.environ))
    assert probe.returncode == 0, probe.stderr
    assert "argcomplete" in probe.stderr


def test_without_argcomplete_one_harmless_line(monkeypatch):
    monkeypatch.setitem(sys.modules, "argcomplete", None)
    for shell in CS.SHELLS:
        text = CS.for_shell(shell)
        assert text.count("\n") == 1
        assert "needs the argcomplete package" in text
        assert "_python_argcomplete" not in text and "__fish_ddflow_complete" not in text


def test_unknown_shell_refused():
    with pytest.raises(ValueError):
        CS.for_shell("powershell")


def test_the_cli_serves_the_same_text(shim):
    probe = subprocess.run(
        [sys.executable, "-m", "ddflow.surfaces.cli", "completion", "zsh"],
        cwd=ROOT,
        env=dict(os.environ, PYTHONPATH=str(ROOT)),
        text=True,
        capture_output=True,
        timeout=120,
    )
    assert probe.stdout == CS.for_shell("zsh"), probe.stderr
