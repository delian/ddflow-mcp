"""A second `ddflow review` of an item whose gate is already being reviewed is refused
(B8664a1e0c6).

A SIGTERMed review recorded both gates "terminated", then a second background review ran
alongside a still-live first -- two processes paying the reviewer for one diff with no lock.
A review now claims the same started-marker a command gate run does (`RUN.claim_started`),
and because the review's record is per ITEM (not per commit) a second review of the item's
gate is refused whatever commit either covers -- the first to start reviews and clears its
marker, and the second sees the live marker and refuses.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ddflow.services.gates import running as RUN

pytestmark = pytest.mark.skipif(os.name != "posix", reason="signals and pids are POSIX")


def _live_twin(repo: Path, item: str, gate: str, sha: str) -> tuple[RUN.Marker, subprocess.Popen]:
    """A marker seen as a live OTHER process on this host, so `live_run` reports a twin.
    The child stays alive for the caller (reap it) so the marker's pid is truly live."""
    child = subprocess.Popen(  # a real live process with a real pid and start time
        [sys.executable, "-c", "import time; time.sleep(120)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    m = RUN.mark_started(repo, item, gate, sha)
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
    import ddflow.api.review.gate as G

    _m, child = _live_twin(tmp_path, "T1", "critic", "tree-aaa")
    try:
        marker, refused = G._claim_or_refuse_twin(tmp_path, "T1", "critic", "tree-aaa")
        assert marker is None and refused is not None
        assert "already being reviewed" in refused.reason and "critic" in refused.reason
    finally:
        _reap(child)


def test_a_second_review_on_another_commit_is_also_refused(tmp_path):
    """The review's record is per item, not per commit: a different commit still races it."""
    import ddflow.api.review.gate as G

    _m, child = _live_twin(tmp_path, "T1", "critic", "tree-aaa")
    try:
        marker, refused = G._claim_or_refuse_twin(tmp_path, "T1", "critic", "tree-bbb")
        assert marker is None and refused is not None
        assert "already being reviewed" in refused.reason
    finally:
        _reap(child)


def test_the_first_review_marks_and_proceeds(tmp_path):
    import ddflow.api.review.gate as G

    marker, refused = G._claim_or_refuse_twin(tmp_path, "T1", "critic", "tree-aaa")
    assert refused is None and marker is not None
    RUN.clear_started(marker)


def test_no_item_or_no_commit_claims_nothing(tmp_path):
    import ddflow.api.review.gate as G

    assert G._claim_or_refuse_twin(tmp_path, "", "critic", "tree-aaa") == (None, None)
    assert G._claim_or_refuse_twin(tmp_path, "T1", "critic", "") == (None, None)
