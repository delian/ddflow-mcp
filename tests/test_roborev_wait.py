"""DX-roborev-wait-no-review: ddflow wraps the roborev wait by polling `roborev list --json`
status, so a job that is merely queued reads as RUNNING (waitable), never as a vanished
one -- `roborev show --job N` prints 'no review found' for an unfinished job, and
`roborev wait --job N` cannot tell live from gone either. The list is the one truthful
source, so the wait polls it and says running vs failed itself."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from ddflow.services import roborev as RR

#: The fake roborev: `list --json` cats `$FAKE_STATUS_FILE` (the test rewrites it between
#: polls), `wait --job` exits $FAKE_WAIT_EXIT, `show --job` prints $FAKE_SHOW_TEXT. Every
#: call is logged with its arguments.
FAKE = r"""#!/bin/sh
echo "$@" >> "$FAKE_LOG"
case "$1" in
  list) cat "$FAKE_STATUS_FILE" ;;
  wait) exit "${FAKE_WAIT_EXIT:-0}" ;;
  show) echo "${FAKE_SHOW_TEXT:-Review for job 7: no issues}" ;;
esac
"""

QUEUED = '[{"id": 7, "status": "queued", "git_ref": "a..abcdef0", "agent": "kilo", "model": "x"}]'
DONE = '[{"id": 7, "status": "done", "git_ref": "a..abcdef0", "agent": "kilo", "model": "x"}]'
FAILED = '[{"id": 7, "status": "failed", "git_ref": "a..abcdef0", "agent": "kilo", "model": ""}]'


def _fake_clock(monkeypatch) -> list[int]:
    """`monotonic` that advances one second per call, so a wait of N seconds is N polls."""
    ticks = [0.0]

    def mono() -> float:
        ticks.append(ticks[-1] + 1.0)
        return ticks[-1]

    monkeypatch.setattr(RR, "monotonic", mono)
    monkeypatch.setattr(RR, "sleep", lambda s: None)
    return ticks


def _setup(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
    repo = tmp_path / "r"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q", "-b", "main"], check=True)
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    exe = bin_ / "roborev"
    exe.write_text(FAKE)
    exe.chmod(0o755)
    log = tmp_path / "log"
    status = tmp_path / "status.json"
    status.write_text(QUEUED)
    monkeypatch.setenv("PATH", f"{bin_}:{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_LOG", str(log))
    monkeypatch.setenv("FAKE_STATUS_FILE", str(status))
    monkeypatch.setenv("FAKE_WAIT_EXIT", "0")
    monkeypatch.setenv("FAKE_SHOW_TEXT", "Review for job 7: no issues")
    return repo, log


def test_a_queued_job_is_polled_until_the_list_says_done(tmp_path, monkeypatch):
    """While the list says queued the wait POLLS (running, never failed); the done flips the
    job's caller-side, and only THEN are `wait`/`show` consulted for the review."""
    repo, log = _setup(tmp_path, monkeypatch)
    _fake_clock(monkeypatch)
    ticks = []

    def flip_once_done() -> None:
        ticks.append("t")
        (tmp_path / "status.json").write_text(DONE)

    out = RR.wait_job(repo, 7, 30, on_tick=flip_once_done)
    calls = log.read_text().splitlines()
    assert out.review is not None and out.review.job == 7 and out.review.reviewer == "x"
    assert out.live is False and out.note == ""
    assert "wait --job 7" in calls and "show --job 7" in calls  # consulted once done


def test_still_running_at_the_bound_says_running_not_failed(tmp_path, monkeypatch):
    repo, _ = _setup(tmp_path, monkeypatch)
    _fake_clock(monkeypatch)
    out = RR.wait_job(repo, 7, 1)
    assert out.review is None and out.live is True
    assert "still queued" in out.note and "running, not failed" in out.note


def test_zero_bound_reads_the_status_once_and_reports_it_live(tmp_path, monkeypatch):
    repo, _ = _setup(tmp_path, monkeypatch)
    _fake_clock(monkeypatch)
    out = RR.wait_job(repo, 7, 0)
    assert out.live is True and out.review is None
    assert "still queued" in out.note


def test_a_failed_job_is_ended_failed_not_a_wait_and_never_a_review(tmp_path, monkeypatch):
    repo, log = _setup(tmp_path, monkeypatch)
    (tmp_path / "status.json").write_text(FAILED)
    _fake_clock(monkeypatch)
    out = RR.wait_job(repo, 7, 30)
    assert out.review is None and out.live is False
    assert "ended failed" in out.note and "re-run" in out.note
    calls = log.read_text().splitlines()
    # an ended job is only ever LISTED: `roborev wait --job` of a failed job is exactly the
    # call that can block or print 'no review found', so it must not be made for one
    assert calls and all(c.startswith("list") for c in calls)


def test_a_done_job_whose_wait_exits_oddly_is_unjudged_not_findings(tmp_path, monkeypatch):
    """roborev wait exits 0 pass / 1 fail, nothing else: a code it never means (a signal, an
    unknown exit) is a verdict nobody gave, so the text is unjudged, never findings."""
    repo, _ = _setup(tmp_path, monkeypatch)
    (tmp_path / "status.json").write_text(DONE)
    monkeypatch.setenv("FAKE_WAIT_EXIT", "2")
    _fake_clock(monkeypatch)
    out = RR.wait_job(repo, 7, 30)
    assert out.review is None and "unjudged" in out.note


def test_a_tick_that_overshoots_the_bound_ends_live_never_a_negative_sleep(tmp_path, monkeypatch):
    """A slow lease renewal that eats the rest of the bound must return the LIVE verdict; a
    negative sleep span would raise and lose the wait's answer."""
    repo, _ = _setup(tmp_path, monkeypatch)
    clock = _fake_clock(monkeypatch)
    start = [None]

    def slow_tick() -> None:
        start.append(1)
        for _ in range(60):
            clock.append(clock[-1] + 1.0)  # the renewal consumes the remaining budget

    def no_negative_sleep(s: float) -> None:
        assert s >= 0, f"sleep({s}) of a negative span past the bound"
        for _ in range(60):
            pass

    monkeypatch.setattr(RR, "sleep", no_negative_sleep)
    monkeypatch.setattr(RR, "monotonic", lambda: clock.append(clock[-1] + 1.0) or clock[-1])
    out = RR.wait_job(repo, 7, 10, on_tick=slow_tick)
    assert out.live is True and "still queued" in out.note and "not failed" in out.note


def test_a_job_the_list_no_longer_has_is_vanished_distinct_from_running(tmp_path, monkeypatch):
    """The title bug: roborev's own 'no review found'. ddflow says the job is GONE (re-enqueue),
    not 'keep waiting' -- a live job names running, a gone one names re-run."""
    repo, _ = _setup(tmp_path, monkeypatch)
    (tmp_path / "status.json").write_text("[]")
    _fake_clock(monkeypatch)
    out = RR.wait_job(repo, 7, 30)
    assert out.review is None and out.live is False
    assert "no job 7" in out.note and "re-run" in out.note


def test_the_trap_primitives_are_never_called_while_the_list_says_queued(tmp_path, monkeypatch):
    """A live job is only ever LISTED: `wait`/`show` fires after the list said done, so their
    'no review found' wording can never classify a running job."""
    repo, log = _setup(tmp_path, monkeypatch)
    clock = _fake_clock(monkeypatch)
    out = RR.wait_job(repo, 7, 3)  # still queued when the (fake) 3s bound runs out
    assert out.live is True
    calls = log.read_text().splitlines()
    assert calls and all(c.startswith("list") for c in calls)
    assert len(clock) >= 3  # it really polled to the bound
