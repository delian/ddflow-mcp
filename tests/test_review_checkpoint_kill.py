"""A review killed mid-round checkpoints the chunks that already answered (bug B982d306cc8).

A ddflow review started in a harness background shell is killed at the background time
limit mid-round; the flat "nothing was reviewed" record lost an hour of reviewer work.
Each chunk's reply is already written as it arrives (bug B206); the fix folds those chunks
back into the record, so a killed review reports ``partial`` and keeps the findings of the
chunks that DID answer instead of dropping them.
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


def _setup(repo: Path, tmp_path: Path) -> tuple[Path, Path]:
    """A reviewer that answers x.py at once with a finding, and sleeps on y.py. `max_concurrency
    = 1` makes the chunks sequential, so chunk 1 (x.py) is checkpointed before chunk 2 (y.py)
    starts; `manifest_max_files = 0` keeps y.py's name out of chunk 1's prompt."""
    began = tmp_path / "began"
    pidfile = tmp_path / "reviewer.pid"
    cli = tmp_path / "reviewer"
    cli.write_text(
        "#!/bin/sh\n"
        "body=$(cat)\n"
        "if echo \"$body\" | grep -q 'y_marker_zz'; then\n"
        f"  echo $$ > '{pidfile}' && touch '{began}' && exec sleep 120\n"
        "fi\n"
        f"printf 'FINDING HIGH x.py:1\\nline one\\n{BODY}\\n\\nSTATUS: FINDINGS 1\\n'\n"
    )
    cli.chmod(cli.stat().st_mode | stat.S_IXUSR)
    run_cli(repo, "init")
    (repo / ".ddflow" / "config.toml").write_text(
        "[worktree]\nenabled = false\n[review]\nmax_rounds = 0\nmanifest_max_files = 0\n"
        f'[[reviewer]]\nname = "r"\nkind = "command"\ncommand = "{cli}"\n'
        'model = "gemini-2.5-pro"\ngates = ["critic"]\n'
        "hedge = 1\nmax_concurrency = 1\nmax_chunk_chars = 100\n"
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ddflow")
    run_cli(repo, "task", "add", "T1", "--title", "t", "--globs", "*.py")
    _git(repo, "checkout", "-q", "-b", "feat")
    (repo / "x.py").write_text("x = 1\n")
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
            _assert_gone(pidfile)
        except BaseException:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(p.pid, signal.SIGKILL)
            raise
    return log.read_text()


def _assert_gone(pidfile: Path) -> None:
    pid = int(pidfile.read_text())
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.1)
    os.kill(pid, signal.SIGKILL)
    raise AssertionError(f"the reviewer (pid {pid}) outlived the terminated review")


def _gate(repo: Path) -> dict:
    code, shown, _err = run_cli(repo, "--json", "show", "T1")
    assert code == 0, shown
    return json.loads(shown)["gates"]["critic"]


def test_a_sigtermed_review_keeps_the_chunk_that_already_answered(repo, tmp_path):
    began, pidfile = _setup(repo, tmp_path)
    text = _kill_when_slow_chunk_starts(repo, began, pidfile, tmp_path)
    assert "terminated by SIGTERM" in text and "not a pass" in text, text

    gate = _gate(repo)
    assert gate["outcome"] == "partial", gate
    assert gate["evidence"]["terminated_by"] == "SIGTERM", gate["evidence"]
    assert gate["evidence"]["reviewed"] == [1], gate["evidence"]
    findings = gate["evidence"]["chunk_findings"]
    assert findings and findings[0]["chunk"] == 1, gate["evidence"]
    assert findings[0]["location"] == "x.py:1" and BODY in findings[0]["detail"], findings
    assert "not a pass" in gate["reason"], gate


def test_a_torn_trailing_multibyte_line_keeps_the_earlier_chunks(tmp_path):
    from ddflow.api.review.reviewers import _ReplyFile

    rf = _ReplyFile(tmp_path, "T1", "critic", "r")
    rf.path.write_bytes(b'{"chunk": 1, "reply": "ok"}\n{"chunk": 2, "reply": "ab' + b"\xe2\x82")
    # ``\xe2\x82`` is a torn multi-byte UTF-8 lead byte: read_text("utf-8") raises a
    # UnicodeDecodeError, which the OSError guard misses, losing chunk 1 too. Line-by-line
    # keeps it; the torn chunk 2 line loses only itself.
    assert rf.chunks() == [(1, "ok")]


def test_a_concurrent_registration_does_not_trip_the_snapshot(tmp_path):
    """DX-review-concurrent-runs: `active_files` snapshots `_ACTIVE` under the lock and
    reads each file's chunks outside it, so a reviewer's worker thread registering a
    sibling reply file while a killed run's read-back iterates drops nothing."""
    import threading

    from ddflow.api.review.reviewers import _ReplyFile, active_files

    first = _ReplyFile(tmp_path, "T1", "critic", "r")
    first.path.parent.mkdir(parents=True, exist_ok=True)
    first.add(1, "ok")
    done = threading.Event()

    def register():
        other = _ReplyFile(tmp_path, "T1", "critic", "q")
        other.path.parent.mkdir(parents=True, exist_ok=True)
        other.add(2, "late")
        done.set()

    t = threading.Thread(target=register)
    t.start()
    t.join(timeout=10)
    assert done.is_set()
    got = active_files("T1", "critic")
    assert first in got, "the killed run's own reply file is in the snapshot"
    chunked = [c for f in got for c, _r in f.chunks()]
    assert 1 in chunked, "the checkpoint read back the chunk that already answered"

