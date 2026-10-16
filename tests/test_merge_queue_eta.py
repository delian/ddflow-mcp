"""An ETA for unfinished work never reads 0 (Ba176075b5f): a fast machine merges in well
under half a second, and `round()` made the estimate for the next entry 0 seconds."""

from __future__ import annotations

import pytest

from ddflow.services import mergequeue as MQ

NOW = 1_000_000.0


def _line(repo, merged_took_s: float):
    done = MQ.Entry(item="T1", seq=1, state=MQ.MERGED, queued_at=NOW, started_at=NOW,
                    finished_at=NOW + merged_took_s)  # fmt: skip
    MQ.save(repo, done)
    MQ.save(repo, MQ.Entry(item="T2", seq=2, queued_at=NOW))
    MQ.save(repo, MQ.Entry(item="T3", seq=3, queued_at=NOW))
    return {r["item"]: r for r in MQ.board(repo, window=5, default_s=60, now=NOW + 5)}


@pytest.mark.parametrize("took", [0.2, 0.01, 0.5])
def test_a_sub_second_merge_still_gives_queued_work_a_positive_eta(tmp_path, took):
    rows = _line(tmp_path, took)
    assert rows["T2"]["eta_s"] == 1 and rows["T3"]["eta_s"] == 1  # 0.2 s x2 rounds UP, not to 0
    assert rows["T3"]["eta_s"] >= rows["T2"]["eta_s"] > 0


def test_a_fixed_estimate_is_the_eta(tmp_path):
    rows = _line(tmp_path, 3.0)
    assert rows["T2"]["eta_s"] == 3 and rows["T3"]["eta_s"] == 6
    assert _line(tmp_path, 3.2)["T2"]["eta_s"] == 4  # a fraction rounds up
