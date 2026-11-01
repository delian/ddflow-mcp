"""A second `ddflow review` of an item already being reviewed is refused (B8664a1e0c6).

A SIGTERMed review recorded both gates "terminated", then a second background review ran
alongside a still-live first -- two processes paying the reviewer for one diff with no lock.
A review now holds a per-ITEM lock (the synthetic "review" marker) across ALL of its gates,
so a second review of the same item is refused whatever gate or commit it covers, until the
first ends.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ddflow.api.review.gate as G
from ddflow.services.gates import running as RUN

pytestmark = pytest.mark.skipif(os.name != "posix", reason="signals and pids are POSIX")


def _live_twin(repo: Path, item: str, sha: str) -> tuple[RUN.Marker, subprocess.Popen]:
    """The per-item review-lock marker seen as a live OTHER process on this host."""
    child = subprocess.Popen(  # a real live process with a real pid and start time
        [sys.executable, "-c", "import time; time.sleep(120)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    m = RUN.mark_started(repo, item, G._REVIEW_LOCK_GATE, sha)
    data = json.loads(Path(m.path).read_text())
    data["pid"] = child.pid
    data["pid_start"] = RUN.proc_start(child.pid)
    Path(m.path).write_text(json.dumps(data))
    return m, child


def _reap(p: subprocess.Popen) -> None:
    if p.poll() is None:
        p.kill()
    p.wait(timeout=30)


def test_a_second_review_is_refused_while_one_lives(tmp_path):
    _m, child = _live_twin(tmp_path, "T1", "tree-aaa")
    try:
        marker, refused = G._claim_or_refuse_twin(tmp_path, "T1", "tree-aaa")
        assert marker is None and refused is not None
        assert "already being reviewed" in refused.reason
    finally:
        _reap(child)


def test_a_second_review_on_another_commit_is_also_refused(tmp_path):
    """The review's record is per item, not per commit: a different commit still races it."""
    _m, child = _live_twin(tmp_path, "T1", "tree-aaa")
    try:
        marker, refused = G._claim_or_refuse_twin(tmp_path, "T1", "tree-bbb")
        assert marker is None and refused is not None
        assert "already being reviewed" in refused.reason
    finally:
        _reap(child)


def test_the_first_review_marks_and_proceeds(tmp_path):
    marker, refused = G._claim_or_refuse_twin(tmp_path, "T1", "tree-aaa")
    assert refused is None and marker is not None
    RUN.clear_started(marker)


def test_no_item_claims_nothing(tmp_path):
    assert G._claim_or_refuse_twin(tmp_path, "", "tree-aaa") == (None, None)
