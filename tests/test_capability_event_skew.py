"""B27842cfecd: a capability event for a release that never stamped the log must not block
an older ddflow, and a ddflow older than the capability must not announce it."""

from __future__ import annotations

from pathlib import Path

import pytest

import ddflow
from ddflow.core import events as E
from ddflow.core.events import Event, SkewRefused
from ddflow.infra.log import EventLog

NEWER = "0.2.4"


def _write(repo: Path, agent: str, kind: str, data: dict, n: int) -> None:
    shard = repo / ".ddflow" / "events"
    shard.mkdir(parents=True, exist_ok=True)
    ev = Event(
        kind=kind,
        subject="ddflow",
        data=data,
        agent=agent,
        lamport=n,
        ts="2099-01-01T00:00:00.000000Z",
    )
    ev = Event(**{**ev.__dict__, "id": ev.compute_id()})
    with (shard / f"{agent}.jsonl").open("a") as fh:
        fh.write(ev.to_json() + "\n")


def _announce(repo: Path, kinds: list[str], *, stamped: bool) -> None:
    """The event a development branch of a newer release wrote into the live log."""
    if stamped:
        _write(
            repo, "dev", "ddflow.seen", {"version": NEWER, "format_level": ddflow.FORMAT_LEVEL}, 1
        )
    _write(
        repo,
        "dev",
        "ddflow.capabilities",
        {"capability": "evidence-blobs", "kinds": kinds, "version": NEWER},
        2,
    )


@pytest.fixture(autouse=True)
def _running_older(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ddflow, "__version__", "0.2.3")


def test_an_unreleased_capability_event_does_not_block_a_gate_record(repo: Path):
    _announce(repo, ["gate.passed", "phase.added"], stamped=False)
    log = EventLog(repo, "me")
    log.append("gate.passed", "T1", {"gate": "x"})
    log.append("phase.added", "P1", {"title": "p"})


def test_a_released_capability_still_refuses_a_direct_conflict(repo: Path):
    _announce(repo, ["gate.passed"], stamped=True)
    log = EventLog(repo, "me")
    with pytest.raises(SkewRefused):
        log.append("gate.passed", "T1", {"gate": "x"})


def test_an_older_version_does_not_announce_a_capability_it_does_not_ship(
    repo: Path, monkeypatch: pytest.MonkeyPatch
):
    cap = E.Capability("evidence-blobs", NEWER, frozenset({"phase.added"}), "test")
    monkeypatch.setitem(E.CAPABILITIES, cap.name, cap)
    monkeypatch.setattr(EventLog, "_active_capabilities", lambda self: {cap.name})
    log = EventLog(repo, "me")
    log.append("phase.added", "P1", {"title": "p"})
    assert [e for e in log.read_all() if e.kind == "ddflow.capabilities"] == []
    monkeypatch.setattr(ddflow, "__version__", NEWER)
    log.append("phase.added", "P2", {"title": "p"})
    (rec,) = [e for e in log.read_all() if e.kind == "ddflow.capabilities"]
    assert rec.data["capability"] == "evidence-blobs"
