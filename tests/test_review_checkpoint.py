"""A review killed mid-round resumes from the next chunk instead of paying every chunk again.

The kill path (bug B982d306cc8) already checkpoints each chunk's reply as it arrives and
records a PARTIAL gate with the reviewed chunks. What it could not do was RESUME: the record
kept the finished chunks, but a re-run re-sent every chunk from scratch. This test pins the
resume (bug Bd8c4e1d8d6): the run after a kill sends only the unreviewed chunk of the SAME
cut, and the finished chunk is kept, not paid twice.
"""

from __future__ import annotations

import contextlib
import json
import os
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from helpers import git as _git

pytestmark = pytest.mark.skipif(os.name != "posix", reason="signals are POSIX")

BODY = "the claim: x is wrong because of y; probe: run z"


def _reviewer_body_answers_fast(tmp_path: Path, pidfile: Path, began: Path, seen: Path) -> str:
    """A one-shot reviewer: chunk 1 (`x_marker_zz`) replies at once, chunk 2 (`y_marker_zz`)
    sleeps so the run can be killed mid-round. Logs every chunk it receives to `seen`."""
    cli = tmp_path / "reviewer"
    cli.write_text(
        "#!/bin/sh\n"
        "body=$(cat)\n"
        f"printf '%s\\n' \"$body\" >> '{seen}'\n"
        "if echo \"$body\" | grep -q 'y_marker_zz'; then\n"
        f"  echo $$ > '{pidfile}' && touch '{began}' && exec sleep 120\n"
        "fi\n"
        f"printf 'FINDING HIGH x.py:1\\nline one\\n{BODY}\\n\\nSTATUS: FINDINGS 1\\n'\n"
    )
    cli.chmod(cli.stat().st_mode | stat.S_IXUSR)
    return str(cli)


def _setup(repo: Path, tmp_path: Path) -> tuple[Path, Path]:
    began = tmp_path / "began"
    pidfile = tmp_path / "reviewer.pid"
    run_cli(repo, "init")
    (repo / ".ddflow" / "config.toml").write_text(
        "[worktree]\nenabled = false\n[review]\nmax_rounds = 0\nmanifest_max_files = 0\n"
        f'[[reviewer]]\nname = "r"\nkind = "command"\ncommand = "{tmp_path / "reviewer"}"\n'
        'model = "gemini-2.5-pro"\ngates = ["critic"]\n'
        "hedge = 1\nmax_concurrency = 1\nmax_chunk_chars = 100\n"
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ddflow")
    run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", "*.py")
    _git(repo, "checkout", "-q", "-b", "feat")
    (repo / "x.py").write_text("x_marker_zz = 1\n")
    (repo / "y.py").write_text("y_marker_zz = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "work")
    return began, pidfile


def _kill_when_slow_chunk_starts(repo: Path, began: Path, pidfile: Path, tmp_path: Path) -> str:
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
    log = tmp_path / "review.out"
    with log.open("w") as sink:
        p = subprocess.Popen(
            [sys.executable, "-m", "ddflow", "--repo", str(repo), "review", "T1", "--gate",
             "critic", "--full", "--branch", "feat"],
            stdout=sink, stderr=subprocess.STDOUT, text=True, env=env,
            start_new_session=True, cwd=str(repo),
        )  # fmt: skip
        try:
            deadline = time.monotonic() + 120
            while not began.exists() and time.monotonic() < deadline and p.poll() is None:
                time.sleep(0.1)
            assert began.exists(), "the slow chunk never started: " + log.read_text()
            os.killpg(p.pid, signal.SIGTERM)
            assert p.wait(timeout=60) != 0, "a killed review must not exit 0"
        except BaseException:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(p.pid, signal.SIGKILL)
            raise
    return log.read_text()


def _gate(repo: Path) -> dict:
    code, shown, _err = run_cli(repo, "--json", "show", "T1")
    assert code == 0, shown
    return json.loads(shown)["gates"]["critic"]


def test_a_killed_review_resumes_from_the_next_chunk(repo, tmp_path):
    seen = tmp_path / "seen.log"
    began, pidfile = _setup(repo, tmp_path)
    _reviewer_body_answers_fast(tmp_path, pidfile, began, seen)
    text = _kill_when_slow_chunk_starts(repo, began, pidfile, tmp_path)
    assert "terminated by SIGTERM" in text, text

    gate = _gate(repo)
    assert gate["outcome"] == "partial", gate
    ev = gate["evidence"]
    assert ev["reviewed"] == [1], ev
    # The finished chunk's FINDING is checkpointed, not just its index (a run that kept the
    # number 1 but dropped the `x.py` finding would still say `reviewed == [1]` yet lose it).
    kept = [
        fd for fd in ev.get("chunk_findings", []) if str(fd.get("location", "")).startswith("x.py")
    ]
    assert kept, ev
    # The resume needs the cut's identity, checkpointed when the run planned its chunks.
    assert ev.get("diff_sha"), ev
    assert ev.get("chunks_total") == 2, ev
    assert ev.get("reviewer") == "r", ev
    assert ev.get("wanted") == [1, 2], ev

    # Replace the reviewer with one that finishes chunk 2, then resume.
    finish = tmp_path / "reviewer"
    finish.write_text(
        "#!/bin/sh\n"
        "body=$(cat)\n"
        f"printf '%s\\n' \"$body\" >> '{seen}'\n"
        f"printf 'FINDING HIGH y.py:1\\nline two\\n{BODY}\\n\\nSTATUS: FINDINGS 1\\n'\n"
    )
    finish.chmod(finish.stat().st_mode | stat.S_IXUSR)

    # Isolate the resume: the killed run already logged chunk 1 and chunk 2's bodies to `seen`,
    # so a resume that never calls the reviewer would still find `y_marker_zz` in the old log.
    seen.write_text("")
    _code, shown, _err = run_cli(
        repo, "review", "T1", "--gate", "critic", "--full", "--branch", "feat"
    )
    gate = _gate(repo)
    got = seen.read_text()
    # The resumed run must re-send ONLY the unreviewed chunk 2, not the finished chunk 1.
    assert "y_marker_zz" in got, "chunk 2 was never sent: " + repr(shown) + "\n" + got
    assert "x_marker_zz" not in got, "chunk 1 was re-sent on resume: " + repr(shown) + "\n" + got
    assert gate["evidence"]["reviewed"] == [1, 2], (gate["evidence"], shown)
    locs = {str(fd.get("location", "")) for fd in gate["evidence"].get("chunk_findings", [])}
    assert any(loc.startswith("x.py") for loc in locs), gate[
        "evidence"
    ]  # chunk 1 survives the resume
    assert any(loc.startswith("y.py") for loc in locs), gate[
        "evidence"
    ]  # chunk 2's finding is kept
