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
    real, confirmed defect header must not read as clean."""
    repo = _setup(tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_WAIT_EXIT", "1")
    monkeypatch.setenv(
        "FAKE_VERDICT",
        "There were no confirmed defects in the helpers.\n"
        "**1. high (CONFIRMED) — a real defect remains**\n",
    )
    r = RB.run(repo, "main", 20)
    assert r.outcome == "findings" and r.exit == 1


def test_a_plain_severity_bullet_next_to_the_phrase_is_still_findings(tmp_path, monkeypatch):
    """A defect severity in roborev's own finding shape (`- medium: ...`) still reads
    findings next to 'no confirmed defects'."""
    repo = _setup(tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_WAIT_EXIT", "1")
    monkeypatch.setenv(
        "FAKE_VERDICT",
        "Review for job 7: no confirmed defects in the helpers.\n- medium: issue here\n",
    )
    r = RB.run(repo, "main", 20)
    assert r.outcome == "findings" and r.exit == 1


def test_a_prose_severity_word_is_not_a_defect(tmp_path, monkeypatch):
    """A severity word mid-sentence ('the high water mark') is prose, not a finding: it
    does not turn a 'no confirmed defects' verdict into findings, even when it wraps to
    start its own line."""
    repo = _setup(tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_WAIT_EXIT", "1")
    monkeypatch.setenv(
        "FAKE_VERDICT",
        "Review for job 7: no confirmed defects found.\n"
        "high water mark is unchanged; the medium term is stable\n",
    )
    r = RB.run(repo, "main", 20)
    assert r.outcome == "clean" and r.exit == 0


def test_a_low_note_next_to_the_phrase_is_still_clean(tmp_path, monkeypatch):
    """A LOW-only note ('Low -- ...: a pre-existing gap', roborev's note severity) stays
    clean: 'low' is a note, not a defect, so it is the clean verdict the fix keeps."""
    repo = _setup(tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_WAIT_EXIT", "1")
    monkeypatch.setenv(
        "FAKE_VERDICT",
        "Review for job 7: no confirmed defects found.\nLow — `f`: a pre-existing gap\n",
    )
    r = RB.run(repo, "main", 20)
    assert r.outcome == "clean" and r.exit == 0


def test_a_defect_on_a_line_saying_not_refuted_is_still_findings(tmp_path, monkeypatch):
    """'not refuted' is not a refutation: a medium defect on a line that says 'not refuted'
    must still read findings. Only the (THEORETICAL)/(REFUTED) verdict sets a defect aside,
    never the word appearing in prose."""
    repo = _setup(tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_WAIT_EXIT", "1")
    monkeypatch.setenv(
        "FAKE_VERDICT",
        "Review for job 7: no confirmed defects.\n"
        "- medium: not refuted — the parser drops the last record\n",
    )
    r = RB.run(repo, "main", 20)
    assert r.outcome == "findings" and r.exit == 1


def test_a_theoretical_verdict_next_to_the_phrase_is_still_clean(tmp_path, monkeypatch):
    """A (THEORETICAL) defect is not a confirmed defect to fix: it reads clean even though
    it carries a medium severity."""
    repo = _setup(tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_WAIT_EXIT", "1")
    monkeypatch.setenv(
        "FAKE_VERDICT",
        "Review for job 7: no confirmed defects found.\n"
        "**1. medium (THEORETICAL) — unverified**\n",
    )
    r = RB.run(repo, "main", 20)
    assert r.outcome == "clean" and r.exit == 0


def test_a_confirmed_defect_next_to_an_unrelated_theoretical_tag_is_findings(
    tmp_path, monkeypatch
):
    """A '(THEORETICAL)' tag elsewhere on the line does not undo a 'high (CONFIRMED)'
    finding next to it: only the verdict right after the severity negates it."""
    repo = _setup(tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_WAIT_EXIT", "1")
    monkeypatch.setenv(
        "FAKE_VERDICT",
        "Author text: no confirmed defects in the helpers.\n"
        "**1. high (CONFIRMED)** — the old (THEORETICAL) tag does not apply; "
        "the branch deletes the wrong table.\n",
    )
    r = RB.run(repo, "main", 20)
    assert r.outcome == "findings" and r.exit == 1
