"""PS-evidence-inline: a lower out-of-line threshold keeps new gate evidence out of the shard
(R-scalability N). Old events are never touched and still replay; blobs stay content-addressed."""

from __future__ import annotations

import pytest

from ddflow.config import LogConfig
from ddflow.core import blobref as B
from ddflow.core.model import fold
from ddflow.infra import blobstore as BS
from ddflow.infra.log import EventLog
from ddflow.services import logblobs as LB
from ddflow.services import logfixture

OLD_THRESHOLD = 1024
#: Typical gate evidence: findings and a tail a few hundred bytes long, under the old threshold.
MID = [{"chunk": 1, "detail": "finding " * 70, "digest": "d1", "title": "t"}]


@pytest.fixture(autouse=True)
def _only_this_tests_projects():
    BS._KNOWN_ROOTS.clear()
    yield
    BS._KNOWN_ROOTS.clear()


def _bytes(root, min_bytes):
    cfg = LogConfig(evidence_blobs=True, evidence_blob_min_bytes=min_bytes)
    log = EventLog(root, "a", log_cfg=cfg)
    log.append("task.added", "T1", {"title": "t", "globs": ["a.py"]})
    for i in range(20):
        log.append(
            "gate.passed",
            "T1",
            {"gate": f"g{i}", "evidence": {"chunk_findings": MID, "tail": "t" * 400 + str(i)}},
        )
    return (root / ".ddflow" / "events" / "a.jsonl").stat().st_size


def test_default_threshold_is_below_the_old_one():
    assert LogConfig().evidence_blob_min_bytes < OLD_THRESHOLD


def test_master_switch_default_is_unchanged(repo):
    """Upgrade-safe: the capability-gated switch is still off, so nothing moves by default."""
    assert LogConfig().evidence_blobs is False
    log = EventLog(repo, "a", log_cfg=LogConfig())
    log.append("task.added", "T1", {"title": "t", "globs": ["a.py"]})
    ev = log.append("gate.passed", "T1", {"gate": "g", "evidence": {"tail": "t" * 5000}})
    assert not B.is_stub(ev.data["evidence"]["tail"])


def test_new_events_are_at_least_30_percent_smaller(tmp_path):
    (a := tmp_path / "old").mkdir()
    (b := tmp_path / "new").mkdir()
    old = _bytes(a, OLD_THRESHOLD)
    new = _bytes(b, LogConfig().evidence_blob_min_bytes)
    assert new <= old * 0.7, (old, new)


def test_blobs_are_content_addressed_and_shared(tmp_path):
    cfg = LogConfig(evidence_blobs=True)
    one = BS.externalize(tmp_path, cfg, "gate.passed", {"evidence": {"tail": "t" * 400}})
    two = BS.externalize(tmp_path, cfg, "gate.passed", {"evidence": {"tail": "t" * 400}})
    ref = one["evidence"]["tail"]["$blob"]
    assert ref == two["evidence"]["tail"]["$blob"] == B.address(B.body("t" * 400))
    assert len(list(BS.BlobStore(tmp_path).refs())) == 1
    assert LB.full(one["evidence"], root=tmp_path)["tail"] == "t" * 400


def test_an_old_log_replays_identically_and_is_not_rewritten(tmp_path):
    events = tmp_path / ".ddflow" / "events"
    logfixture.generate(events, 1500, seed=7)
    before = {p.name: p.read_bytes() for p in events.glob("*.jsonl")}
    first = fold(EventLog(tmp_path).read_all(), strict=False)
    cfg = LogConfig(evidence_blobs=True)  # the new default threshold, switch on
    again = fold(EventLog(tmp_path, log_cfg=cfg).read_all(), strict=False)
    assert first.items.keys() == again.items.keys()
    assert {k: v.gates for k, v in first.items.items()} == {
        k: v.gates for k, v in again.items.items()
    }
    assert {p.name: p.read_bytes() for p in events.glob("*.jsonl")} == before
