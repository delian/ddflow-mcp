"""B2bd9e8b715: a roborev wait exit 1 whose review names no CONFIRMED defect reads as CLEAN.

`roborev wait --job N` exits 1 on any finding, so a review whose only findings are a LOW
note on a pre-existing gap ('no CONFIRMED defects') is a clean verdict, not a `findings`
one -- the copy-paste `gate record --outcome failed` line overstates it and the agent has to
overrule it by hand.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from ddflow.services import roborev_branch as RB

FAKE = r"""#!/bin/sh
case "$1" in
  review) echo "Enqueued job 7 for range" ;;
  wait) exit "${FAKE_WAIT_EXIT:-0}" ;;
  list) echo "[{\"id\": 7, \"status\": \"done\", \"git_ref\": \"x..abcdef0\", \"agent\": \"kilo\"}]" ;;
  show) echo "${FAKE_VERDICT:-Review for job 7: no confirmed defects}" ;;
esac
"""


def _setup(tmp_path: Path, monkeypatch) -> Path:
    repo = tmp_path / "r"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q", "-b", "main"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "a@b"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "a"], check=True)
    (repo / "f").write_text("1")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "base"], check=True)
    subprocess.run(["git", "-C", str(repo), "checkout", "-qb", "work"], check=True)
    (repo / "f").write_text("2")
    subprocess.run(["git", "-C", str(repo), "commit", "-qam", "c"], check=True)
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    exe = bin_ / "roborev"
    exe.write_text(FAKE)
    exe.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_}:{os.environ['PATH']}")
    return repo


def test_a_findings_exit_with_no_confirmed_defect_is_clean(tmp_path, monkeypatch):
    repo = _setup(tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_WAIT_EXIT", "1")
    monkeypatch.setenv("FAKE_VERDICT", "Review for job 7: no confirmed defects (one LOW note)")
    r = RB.run(repo, "main", 20)
    assert r.outcome == "clean", r.outcome
    assert r.exit == 0


def test_a_findings_exit_with_a_real_defect_is_still_findings(tmp_path, monkeypatch):
    repo = _setup(tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_WAIT_EXIT", "1")
    monkeypatch.setenv("FAKE_VERDICT", "Review for job 7: a medium confirmed defect")
    r = RB.run(repo, "main", 20)
    assert r.outcome == "findings" and r.exit == 1


def test_a_negated_no_confirmed_phrase_next_to_a_real_defect_is_still_findings(
    tmp_path, monkeypatch
):
    """The reviewer's refutation case: the 'no CONFIRMED defects' phrase in prose next to a
    real, confirmed severity bullet must not read as clean."""
    repo = _setup(tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_WAIT_EXIT", "1")
    monkeypatch.setenv(
        "FAKE_VERDICT",
        "There were no confirmed defects in the helpers.\n"
        "**high** ddflow/services/roborev_branch.py:255\nA real defect remains.\n",
    )
    r = RB.run(repo, "main", 20)
    assert r.outcome == "findings" and r.exit == 1
