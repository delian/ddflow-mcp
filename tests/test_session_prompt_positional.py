"""B-session-prompt-positional-hangs / B-session-prompt-cli-needs-session-id: the positional
of `ddflow session prompt|note` is the SESSION id. Words passed there used to be taken as an
id and the text then read from stdin, which blocks forever on an open pipe (an agent's
stdin). A positional that is not a known session is refused with a pointer to --text, and
stdin is not read; with no positional the latest open session takes the words, as in MCP."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]


def _run(repo: Path, *argv: str, stdin: str | None = None, hang_s: float = 60):
    """Run the CLI with an OPEN stdin pipe (as an agent has) unless `stdin` is given."""
    env = {**os.environ, "PYTHONPATH": str(_ROOT)}
    p = subprocess.Popen(
        [sys.executable, "-m", "ddflow", "--repo", str(repo), *argv],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    try:
        if stdin is not None:
            out, err = p.communicate(stdin, timeout=hang_s)
        else:
            out, err = p.communicate(timeout=hang_s)  # communicate() closes stdin: use wait
    except subprocess.TimeoutExpired:
        p.kill()
        p.communicate()
        pytest.fail(f"`ddflow {' '.join(argv)}` blocked (reading stdin?)")
    return p.returncode, out, err


@pytest.mark.parametrize("cmd", ["prompt", "note"])
@pytest.mark.parametrize("positional", ["the operator said hello world", "hello"])
def test_words_in_the_session_slot_are_refused_not_read_from_stdin(repo, cmd, positional):
    import threading

    env = {**os.environ, "PYTHONPATH": str(_ROOT)}
    p = subprocess.Popen(
        [sys.executable, "-m", "ddflow", "--repo", str(repo), "session", cmd, positional],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    # stdin is open and silent: a command that reads it never returns
    t = threading.Timer(90, p.kill)
    t.start()
    try:
        err = p.stderr.read()
        p.wait()
    finally:
        t.cancel()
        p.stdin.close()
    assert p.returncode not in (0, -9), (p.returncode, err)
    assert "--text" in err and "session" in err, err


def test_text_flag_without_a_session_uses_the_latest_open_one(repo):
    code, out, err = _run(repo, "session", "start")
    assert code == 0, err
    sid = out.strip().splitlines()[0]
    code, out, err = _run(repo, "session", "prompt", "--text", "hello there", stdin="")
    assert code == 0, err
    assert sid in out or "latest" in out or "recorded" in out


def test_a_known_session_id_still_takes_piped_text(repo):
    code, out, err = _run(repo, "session", "start")
    sid = out.strip().splitlines()[0]
    code, out, err = _run(repo, "session", "prompt", sid, stdin="piped words")
    assert code == 0, err
