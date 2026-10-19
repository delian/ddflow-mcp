"""B37468487fb: the standards gate's roborev run reviews the COMBINED branch diff with a
bounded, configurable wait, and says why when it gives nothing."""

from __future__ import annotations

import subprocess
from pathlib import Path

from ddflow.services import roborev_branch as RB

FAKE = r"""#!/bin/sh
echo "$@" >> "$FAKE_LOG"
case "$1" in
  review) echo "Enqueued job 7 for range" ;;
  wait) [ -n "$FAKE_HANG" ] && sleep 30; exit "${FAKE_WAIT_EXIT:-0}" ;;
  list) [ -n "$FAKE_LIST_EMPTY" ] && { echo "[]"; exit 0; }; echo "[{\"id\": 7, \"status\": \"${FAKE_STATUS-done}\", \"git_ref\": \"x..abcdef0\"}]" ;;
  show) [ -n "$FAKE_SHOW_FAIL" ] && exit 1; echo "Review for job 7: ${FAKE_VERDICT:-no issues}" ;;
esac
"""


def _git(d: Path, *a: str) -> None:
    subprocess.run(["git", "-C", str(d), *a], check=True, capture_output=True)


def _setup(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
    repo = tmp_path / "r"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "a@b")
    _git(repo, "config", "user.name", "a")
    (repo / "f").write_text("1")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    _git(repo, "checkout", "-qb", "work")
    for i in range(2):
        (repo / "f").write_text(str(i + 2))
        _git(repo, "commit", "-qam", f"c{i}")
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    exe = bin_ / "roborev"
    exe.write_text(FAKE)
    exe.chmod(0o755)
    log = tmp_path / "log"
    monkeypatch.setenv("PATH", f"{bin_}:{__import__('os').environ['PATH']}")
    monkeypatch.setenv("FAKE_LOG", str(log))
    return repo, log


def test_one_range_job_covers_the_whole_branch(tmp_path, monkeypatch):
    repo, log = _setup(tmp_path, monkeypatch)
    r = RB.run(repo, "main", 20)
    assert r.outcome == "clean" and r.job == 7 and r.exit == 0
    calls = log.read_text().splitlines()
    base = subprocess.run(
        ["git", "-C", str(repo), "merge-base", "main", "HEAD"], capture_output=True, text=True
    ).stdout.strip()
    assert calls[0] == f"review --since {base}"  # one combined job, no --branch/--local
    assert "wait --job 7" in calls


def test_no_result_in_time_is_unavailable_with_the_job_and_the_reason(tmp_path, monkeypatch):
    repo, _log = _setup(tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_HANG", "1")
    r = RB.run(repo, "main", 1)
    assert r.outcome == "unavailable" and r.exit == 2 and r.job == 7
    assert "no result in 1s" in r.reason and "roborev wait --job 7" in r.reason
    assert "standards_wait_s" in r.reason


def test_a_fail_verdict_is_findings_not_unavailable(tmp_path, monkeypatch):
    repo, _log = _setup(tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_WAIT_EXIT", "1")
    monkeypatch.setenv("FAKE_VERDICT", "a bug")
    r = RB.run(repo, "main", 20)
    assert r.outcome == "findings" and r.exit == 1 and "a bug" in r.text


def test_a_job_that_errored_is_unavailable_not_findings(tmp_path, monkeypatch):
    repo, _log = _setup(tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_WAIT_EXIT", "1")
    monkeypatch.setenv("FAKE_STATUS", "failed")
    monkeypatch.setenv("FAKE_VERDICT", "worker error")  # show prints the job record anyway
    r = RB.run(repo, "main", 20)
    assert r.outcome == "unavailable" and r.exit == 2 and "ended failed" in r.reason


def test_a_fail_verdict_whose_show_gives_nothing_is_still_findings(tmp_path, monkeypatch):
    repo, _log = _setup(tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_WAIT_EXIT", "1")
    monkeypatch.setenv("FAKE_SHOW_FAIL", "1")  # `roborev show` exits 1 and prints nothing
    r = RB.run(repo, "main", 20)
    assert r.outcome == "findings" and r.exit == 1


def test_the_wait_is_a_knob():
    from ddflow.config import Config

    assert Config().gates.standards_wait_s == 900


def test_a_job_missing_from_the_list_is_unavailable_and_keeps_the_text(tmp_path, monkeypatch):
    """No readable status: never `findings` on the strength of printed text alone."""
    repo, _log = _setup(tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_WAIT_EXIT", "1")
    monkeypatch.setenv("FAKE_LIST_EMPTY", "1")  # `roborev list` no longer names the job
    monkeypatch.setenv("FAKE_VERDICT", "a bug")
    r = RB.run(repo, "main", 20)
    assert r.outcome == "unavailable" and "unjudged" in r.reason and "a bug" in r.text


def test_the_job_id_is_the_number_after_the_word_job_not_any_digit(tmp_path, monkeypatch):
    repo, _log = _setup(tmp_path, monkeypatch)
    assert RB._find_job("roborev", repo, "a" * 40, "queued review job for 3 commits as job #9") == 9


def test_a_job_missing_from_the_list_and_no_review_is_unavailable(tmp_path, monkeypatch):
    repo, _log = _setup(tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_WAIT_EXIT", "1")
    monkeypatch.setenv("FAKE_LIST_EMPTY", "1")
    monkeypatch.setenv("FAKE_SHOW_FAIL", "1")
    r = RB.run(repo, "main", 20)
    assert r.outcome == "unavailable" and "unknown state" in r.reason


def test_every_wait_hint_names_the_job_flag(tmp_path, monkeypatch):
    """B-baedb82a4c5: `roborev wait 3023` reads the number as a sha prefix ("No job found");
    the hints must say `--job`."""
    repo, _log = _setup(tmp_path, monkeypatch)
    r = RB.run(repo, "main", 0)
    assert "roborev wait --job 7" in r.reason
    from ddflow.services import roborev as RR

    sha = "a" * 40
    note = RR._unfinished_note([{"id": 12, "git_ref": sha, "status": "running"}], sha)
    assert "`roborev wait --job 12`" in note
