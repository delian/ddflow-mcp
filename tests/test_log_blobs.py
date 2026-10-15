"""Out-of-line gate evidence (B-gap-log-scale): the shard keeps a reference, the blob holds
the value, every reader that needs the value reads it back, and a blob that cannot be read
is reported, never taken for empty."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from ddflow.config import LogConfig
from ddflow.core import blobref as B
from ddflow.core.model import fold
from ddflow.infra import blobstore as BS
from ddflow.infra.log import EventLog
from ddflow.services import eventcommit as EC
from ddflow.services import logblobs as LB
from ddflow.services import logfixture
from ddflow.services.gates.outcomes import triage_counts

import pytest


@pytest.fixture(autouse=True)
def _only_this_tests_projects():
    """Blobs are found by hash in every project the process opened; another test's identical
    blob would answer for a removed one."""
    BS._KNOWN_ROOTS.clear()
    yield
    BS._KNOWN_ROOTS.clear()


ON = LogConfig(evidence_blobs=True)
BIG = [{"chunk": 1, "detail": "finding " * 400, "digest": "d1", "title": "t"}]


def _gate(log: EventLog, kind="gate.failed", **ev):
    log.append("task.added", "T1", {"title": "t", "globs": ["a.py"]})
    return log.append(kind, "T1", {"gate": "critic", "reason": "r", "evidence": {"status": "REVIEWED", **ev}})


def test_big_evidence_leaves_the_shard_and_reads_back(repo):
    log = EventLog(repo, "a", log_cfg=ON)
    ev = _gate(log, chunk_findings=BIG, tail="x" * 5000)
    assert B.is_stub(ev.data["evidence"]["chunk_findings"]) and B.is_stub(ev.data["evidence"]["tail"])
    assert ev.data["evidence"]["status"] == "REVIEWED"  # small keys stay
    shard = (repo / ".ddflow" / "events" / "a.jsonl").read_text()
    assert "finding finding" not in shard and "xxxxxxxx" not in shard
    st = fold(log.read_all(), strict=False)
    rec = st.items["T1"].gates["critic"]
    full = LB.full(rec.evidence)
    assert full["chunk_findings"] == BIG and full["tail"] == "x" * 5000
    assert log.verify() == []  # event ids still match their (reference-carrying) bodies


def test_small_values_and_a_disabled_knob_stay_inline(repo):
    log = EventLog(repo, "a", log_cfg=ON)
    ev = _gate(log, chunk_findings=[{"detail": "short"}])
    assert ev.data["evidence"]["chunk_findings"] == [{"detail": "short"}]
    off = EventLog(repo, "b", log_cfg=LogConfig())
    big = off.append("gate.failed", "T1", {"gate": "ci", "evidence": {"tail": "y" * 5000}})
    assert big.data["evidence"]["tail"] == "y" * 5000
    keyed = EventLog(repo, "c", log_cfg=LogConfig(evidence_blobs=True, evidence_blob_keys=["extra"]))
    ev = keyed.append("gate.failed", "T1", {"gate": "ci", "evidence": {"tail": "z" * 5000, "extra": "q" * 5000}})
    assert ev.data["evidence"]["tail"] == "z" * 5000 and B.is_stub(ev.data["evidence"]["extra"])


def test_only_gate_events_move_evidence(repo):
    log = EventLog(repo, "a", log_cfg=ON)
    ev = log.append("cadence.ran", "c1", {"result": "ok", "evidence": {"tail": "x" * 5000}})
    assert ev.data["evidence"]["tail"] == "x" * 5000


def test_a_missing_blob_is_unavailable_never_empty(repo):
    log = EventLog(repo, "a", log_cfg=ON)
    ev = _gate(log, chunk_findings=BIG)
    ref = ev.data["evidence"]["chunk_findings"]["$blob"]
    BS.BlobStore(repo).remove(ref)
    full = LB.full(fold(log.read_all(), strict=False).items["T1"].gates["critic"].evidence)
    got = full["chunk_findings"]
    assert isinstance(got, B.Unavailable) and got and "missing" in got
    assert full["blobs_unavailable"] == ["chunk_findings"]
    assert any("missing" in p for p in LB.problems(repo, log.read_all()))


def test_a_tampered_blob_is_detected(repo):
    log = EventLog(repo, "a", log_cfg=ON)
    ev = _gate(log, tail="x" * 5000)
    path = BS.BlobStore(repo).path(ev.data["evidence"]["tail"]["$blob"])
    path.write_text(json.dumps("y" * 5000))
    got = LB.full(ev.data["evidence"])["tail"]
    assert isinstance(got, B.Unavailable) and "altered" in got
    assert LB.problems(repo, log.read_all())


def test_triage_counts_read_the_blob_and_hold_when_it_is_gone(repo):
    log = EventLog(repo, "a", log_cfg=ON)
    ev = _gate(log, chunk_findings=BIG)
    it = fold(log.read_all(), strict=False).items["T1"]
    assert triage_counts(it, "critic") == {"findings": 1, "refuted": 0, "confirmed": 0, "untriaged": 1}
    BS.BlobStore(repo).remove(ev.data["evidence"]["chunk_findings"]["$blob"])
    gone = triage_counts(it, "critic")
    assert gone is not None and gone["untriaged"] == 1 and gone["unavailable"] == 1


def test_the_log_with_blobs_is_smaller_and_every_value_comes_back(tmp_path):
    inline = tmp_path / "inline"
    logfixture.generate(inline / ".ddflow" / "events", 3000, seed=4)
    src = EventLog(inline).read_all()
    out = EventLog(tmp_path / "blobs", "w", log_cfg=ON)
    out.stamp = False
    blobbed = 0
    for e in src:
        if e.kind in B.KINDS:
            data = BS.externalize(tmp_path / "blobs", out.log_cfg, e.kind, e.data)
            ev = data.get("evidence", {})
            blobbed += sum(1 for _ in B.stubs(ev))
            assert LB.full(ev, root=tmp_path / "blobs") == e.data["evidence"]
    assert blobbed
    inline_bytes = sum(len(json.dumps(e.data)) for e in src if e.kind in B.KINDS)
    stub_bytes = sum(
        len(json.dumps(BS.externalize(tmp_path / "blobs", out.log_cfg, e.kind, e.data)))
        for e in src if e.kind in B.KINDS
    )
    assert stub_bytes < inline_bytes * 0.7


def test_the_event_commit_takes_the_blobs_with_the_shard(repo):
    log = EventLog(repo, "a", log_cfg=ON)
    _gate(log, tail="x" * 5000)
    sha, note = EC.commit_shards(repo, "test")
    assert sha, note
    tracked = subprocess.run(["git", "-C", str(repo), "ls-files", ".ddflow/blobs"], capture_output=True, text=True).stdout
    assert tracked.strip().endswith(".json")
    assert EC.uncommitted_blobs(repo) == []


def test_the_capability_is_recorded_with_the_first_gate_write(repo, monkeypatch):
    import ddflow
    from ddflow.core import events as E

    # only the release that ships a capability announces it (B27842cfecd)
    monkeypatch.setattr(ddflow, "__version__", E.CAPABILITIES[E.CAP_EVIDENCE_BLOBS].since)

    log = EventLog(repo, "a", log_cfg=ON)
    _gate(log, tail="x" * 5000)
    caps = [e.data["capability"] for e in log.read_all() if e.kind == "ddflow.capabilities"]
    assert E.CAP_EVIDENCE_BLOBS in caps
    assert E.CAPABILITIES[E.CAP_EVIDENCE_BLOBS].kinds == B.KINDS


def test_it_is_off_by_default_and_then_records_no_capability(repo):
    """The rollout guard: a project that has not turned it on writes evidence inline and
    never records the capability that would refuse an older ddflow on a shared log."""
    from ddflow.core import events as E

    log = EventLog(repo, "a")
    ev = _gate(log, tail="x" * 5000)
    assert ev.data["evidence"]["tail"] == "x" * 5000
    caps = [e.data["capability"] for e in log.read_all() if e.kind == "ddflow.capabilities"]
    assert E.CAP_EVIDENCE_BLOBS not in caps
    assert not (repo / ".ddflow" / "blobs").exists()
