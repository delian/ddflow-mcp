"""B435bbddb18: an outage delta re-records the earlier reviewed round (B277e2a224f), and
that copy carried the round's `review_kind`, so the budget counted one round twice."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_review_delta import OK, _fix, _review, _setup

import ddflow.api.review as api
from ddflow.core.model import fold
from ddflow.infra.log import EventLog


def _item(repo: Path):
    return fold(EventLog(repo).read_all(), strict=False).items["T1"]


def test_an_outage_delta_after_a_full_pass_leaves_rounds_used_at_one(repo, tmp_path):
    _setup(repo, tmp_path)
    (tmp_path / "fake-reviewer").write_text(
        "#!/bin/sh\ncat >/dev/null\necho 'STATUS: NO FINDINGS'\n"
    )
    assert _review(repo).exit == OK
    assert api._rounds_used(_item(repo), "critic") == 1
    tree = _item(repo).gates["critic"].evidence["tree_sha"]
    _fix(repo, "a.py")
    (tmp_path / "fake-reviewer").write_text("#!/bin/sh\nexit 1\n")  # the reviewer is down
    assert _review(repo).exit != OK
    it = _item(repo)
    # the kept pass still describes the tree the reviewer saw, so `complete` reads it stale
    assert it.gates["critic"].evidence["tree_sha"] == tree
    assert it.gates["critic"].outcome == "passed"
    assert api._rounds_used(it, "critic") == 1
    assert api._full_rounds(it, "critic") == 1 and api._delta_rounds(it, "critic") == 0


def test_an_outage_after_a_delta_round_does_not_count_that_delta_twice(repo, tmp_path):
    _setup(repo, tmp_path)
    (tmp_path / "fake-reviewer").write_text(
        "#!/bin/sh\ncat >/dev/null\necho 'STATUS: NO FINDINGS'\n"
    )
    assert _review(repo).exit == OK
    _fix(repo, "a.py")
    assert _review(repo).exit == OK  # a delta that reached the reviewer
    assert api._delta_rounds(_item(repo), "critic") == 1
    _fix(repo, "b.py")
    (tmp_path / "fake-reviewer").write_text("#!/bin/sh\nexit 1\n")
    assert _review(repo).exit != OK
    it = _item(repo)
    assert api._full_rounds(it, "critic") == 1 and api._delta_rounds(it, "critic") == 1


def test_a_copy_written_before_the_marker_existed_is_not_counted_either():
    from types import SimpleNamespace as NS

    def run(**ev):
        return NS(evidence={"review_kind": "full", **ev})

    it = NS(runs_of=lambda _g: [run(), run(unavailable_deltas=[{"reason": "down"}])])
    assert api._full_rounds(it, "critic") == 1
