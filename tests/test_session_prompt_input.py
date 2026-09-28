"""`ddflow session prompt` never records an empty prompt, and never waits on a terminal.

Bug B1443180519: the positional is the SESSION id and the words come from `--text` or
stdin. The natural misreading -- `ddflow session prompt "<operator words>"` -- stored the
words as a session id and read stdin: at EOF that was the empty string, printed as
`recorded (0 redaction(s))`, and on a live terminal it blocked forever. Three operator
prompts were recorded empty on 2026-09-28 and a fourth hung for over two minutes. The
log is what `ddflow replay` rebuilds the project from; an empty prompt in it is a hole
that says it is not one.
"""

from __future__ import annotations

import os
import pty
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.api import knowledge as A
from ddflow.infra.log import EventLog

ROOT = Path(__file__).resolve().parents[1]


def _cli(repo: Path, *argv: str, stdin=subprocess.DEVNULL, data: str | None = None, timeout=60):
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    args = [sys.executable, "-m", "ddflow", "--repo", str(repo), *argv]
    if data is not None:
        stdin = None
    p = subprocess.run(
        args, input=data, stdin=stdin, capture_output=True, text=True, env=env, timeout=timeout
    )
    return p.returncode, p.stdout, p.stderr


def _prompts(repo: Path) -> list[dict]:
    return [e.data for e in EventLog(repo, "reader").read_all() if e.kind == "session.prompt"]


def _session(repo: Path) -> str:
    assert run_cli(repo, "init")[0] == 0
    code, out, _err = run_cli(repo, "session", "start")
    assert code == 0
    return out.strip().splitlines()[-1]


def test_the_misread_form_at_eof_refuses_and_records_nothing(repo):
    _session(repo)
    code, out, err = _cli(repo, "session", "prompt", "Fix all the bugs in the bug list")
    assert code != 0, out + err
    assert "recorded" not in out, out
    assert "--text" in err and "session id" in err.lower(), err
    assert _prompts(repo) == []


def test_empty_text_is_refused(repo):
    sid = _session(repo)
    code, _out, err = _cli(repo, "session", "prompt", sid, "--text", "   ")
    assert code != 0
    assert "empty" in err.lower(), err
    assert _prompts(repo) == []


def test_a_terminal_stdin_is_refused_instead_of_blocking(repo):
    """A tty on stdin is a person, not a pipe: reading it waits for them forever."""
    sid = _session(repo)
    leader, follower = pty.openpty()
    try:
        code, _out, err = _cli(repo, "session", "prompt", sid, stdin=follower, timeout=30)
    finally:
        os.close(leader)
        os.close(follower)
    assert code != 0
    assert "--text" in err, err
    assert _prompts(repo) == []


def test_piped_text_is_still_recorded(repo):
    """The reason stdin is read at all: multi-line prompts a shell would mangle."""
    sid = _session(repo)
    code, out, err = _cli(repo, "session", "prompt", sid, data="line one\n$HOME `x`\n")
    assert code == 0, out + err
    assert [p["text"] for p in _prompts(repo)] == ["line one\n$HOME `x`\n"]


def test_text_flag_is_recorded(repo):
    sid = _session(repo)
    assert _cli(repo, "session", "prompt", sid, "--text", "do the thing")[0] == 0
    assert [p["text"] for p in _prompts(repo)] == ["do the thing"]


def test_the_api_refuses_empty_text_for_every_surface(repo):
    """MCP calls the API directly; the refusal must live there, not only in argv."""
    sid = _session(repo)
    out = A.session_prompt(repo, sid, "", agent="t")
    assert out.exit != 0
    assert _prompts(repo) == []


def test_an_empty_note_is_refused_too(repo):
    sid = _session(repo)
    code, _out, err = _cli(repo, "session", "note", sid)
    assert code != 0, err
    assert [e for e in EventLog(repo, "r").read_all() if e.kind == "session.note"] == []
