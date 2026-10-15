"""Log scale (B-gap-log-scale): the fold and the log stay fast and give the same answers.

Every optimisation here is pinned against the unoptimised path on a generated log."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from ddflow.core.model import fold
from ddflow.core.records import Lease, _claim
from ddflow.infra.log import EventLog
from ddflow.services import logfixture


def _log(tmp_path: Path, n: int = 1500) -> EventLog:
    logfixture.generate(tmp_path / ".ddflow" / "events", n, seed=3)
    return EventLog(tmp_path)


def test_fixture_has_the_real_logs_shape(tmp_path):
    log = _log(tmp_path, 3000)
    kinds = [e.kind for e in log.read_all()]
    share = kinds.count("lease.renewed") / len(kinds)
    assert 0.35 < share < 0.6  # the real log: 43%


def test_claim_window_equals_asdict_and_is_a_copy():
    lease = Lease("a", 1.0, 2.0, 30, globs=["x"], resources=["gpu:1"], event="e1", clone="c")
    claim = _claim(lease)
    assert claim == {"holder": "a", "event": "e1", "lease": asdict(lease)}
    claim["lease"]["globs"].append("y")
    claim["lease"]["renewed_at"] = 99.0
    assert lease.globs == ["x"] and lease.renewed_at == 2.0


def test_fold_of_the_generated_log_is_stable(tmp_path):
    log = _log(tmp_path)
    a = fold(log.read_all(), strict=False)
    b = fold(log.read_all(), strict=False)
    assert repr(a.items) == repr(b.items)
    assert a.items  # the fixture folds to something


def _asdict_claim(lease):
    return {"holder": lease.holder, "event": lease.event, "lease": asdict(lease)}


def test_fold_is_byte_identical_to_the_asdict_claim_on_a_contested_log(tmp_path, monkeypatch):
    """The flat claim copy changes nothing the fold computes: same State, lease windows
    and contests, on a generated log and on this repository's own log when it has one."""
    from ddflow.core import records
    from ddflow.core.handlers import leases as H

    logs = [_log(tmp_path)]
    real = Path(__file__).resolve().parents[1]
    if (real / ".ddflow" / "events").is_dir():
        logs.append(EventLog(real))
    for log in logs:
        events = log.read_all()
        now = repr(fold(events, strict=False).items)
        with monkeypatch.context() as m:
            m.setattr(records, "_claim", _asdict_claim)
            m.setattr(H, "_claim", _asdict_claim)
            before = repr(fold(events, strict=False).items)
        assert now == before
