"""A second `ddflow review` of the same item on the same commit is refused while the first
is live (B8664a1e0c6).

A SIGTERMed review recorded both gates "terminated", then a second background review ran
alongside a still-live first -- two processes paying the reviewer for one diff with no lock.
A review now claims the same started-marker a command gate run does (`RUN.claim_started`):
the first to start reviews and clears its marker, and the second sees the live marker and
refuses. A review of a NEWER commit is a different run and is allowed.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ddflow.services.gates import running as RUN

pytestmark = pytest.mark.skipif(os.name != "posix", reason="signals and pids are POSIX")


def _live_twin(repo: Path, item: str, gate: str, sha: str) -> RUN.Marker:
    """A marker seen as a live OTHER process on this host, so `live_run` reports a twin."""
    m = RUN.mark_started(repo, item, gate, sha)
    data = json.loads(Path(m.path).read_text())
    data["pid"] = os.getppid()
    data["pid_start"] = RUN.proc_start(os.getppid())
    Path(m.path).write_text(json.dumps(data))
    return m


def test_a_second_review_on_the_same_commit_is_refused_claiming_the_live_one(tmp_path):
    import ddflow.api.review.gate as G

    _live_twin(tmp_path, "T1", "critic", "tree-aaa")
    marker, refused = G._claim_or_refuse_twin(tmp_path, "T1", "critic", "tree-aaa")
    assert marker is None and refused is not None
    assert "already being reviewed" in refused.reason and "critic" in refused.reason


def test_a_review_on_a_newer_commit_is_allowed_and_marks(tmp_path):
    import ddflow.api.review.gate as G

    _live_twin(tmp_path, "T1", "critic", "tree-aaa")
    marker, refused = G._claim_or_refuse_twin(tmp_path, "T1", "critic", "tree-bbb")
    assert refused is None and marker is not None
    RUN.clear_started(marker)


def test_no_item_or_no_commit_claims_nothing(tmp_path):
    import ddflow.api.review.gate as G

    assert G._claim_or_refuse_twin(tmp_path, "", "critic", "tree-aaa") == (None, None)
    assert G._claim_or_refuse_twin(tmp_path, "T1", "critic", "") == (None, None)
