"""The per-clone Lamport high-water mark (B-gap-log-scale): appends stop loading the whole
log, and the clock is exactly what the full read gives -- including when a shard changed
under it."""

from __future__ import annotations

import json
from pathlib import Path

from ddflow.infra.log import EventLog
from ddflow.services import logfixture

LOCAL = Path(".ddflow") / "local" / "shardmeta"


def LOCAL_DIR_FILES(repo: Path):
    return sorted((repo / LOCAL).glob("*.json"))


def _full(repo: Path) -> int:
    return max(
        (
            json.loads(ln)["lamport"]
            for f in (repo / ".ddflow" / "events").glob("*.jsonl")
            for ln in f.read_text().splitlines()
            if ln.strip()
        ),
        default=0,
    )


def _project(tmp_path: Path, n: int = 800) -> Path:
    logfixture.generate(tmp_path / ".ddflow" / "events", n, seed=2)
    return tmp_path


def test_an_append_continues_the_clock_and_leaves_a_mark(tmp_path):
    repo = _project(tmp_path)
    before = _full(repo)
    ev = EventLog(repo, "writer").append("session.note", "S1", {"text": "x"})
    assert ev.lamport > before  # past every shard (the version stamp is written first)
    assert any(LOCAL_DIR_FILES(repo))
    again = EventLog(repo, "writer").append("session.note", "S1", {"text": "y"})
    assert again.lamport == ev.lamport + 1


def test_a_mark_is_not_read_past_a_shard_that_changed(tmp_path):
    """Another clone's events land in a shard (a merge appends): the next append is past them,
    even though this clone's mark still says the shard's old maximum."""
    repo = _project(tmp_path)
    log = EventLog(repo, "writer")
    log.append("session.note", "S1", {"text": "x"})
    shard = next((repo / ".ddflow" / "events").glob("synth-1.jsonl"))
    line = json.loads(shard.read_text().splitlines()[0])
    line.update(lamport=_full(repo) + 500, id="eforged")
    with shard.open("a") as f:
        f.write(json.dumps(line) + "\n")
    fresh = EventLog(repo, "writer").append("session.note", "S1", {"text": "z"})
    assert fresh.lamport > line["lamport"]


def test_a_shard_with_its_maximum_not_at_its_tail_is_counted(tmp_path):
    repo = _project(tmp_path)
    EventLog(repo, "writer").append("session.note", "S1", {"text": "x"})  # leaves a mark
    shard = repo / ".ddflow" / "events" / "synth-2.jsonl"
    rows = shard.read_text().splitlines()
    big = json.loads(rows[0])
    big.update(lamport=_full(repo) + 90, id="ebig")
    low = json.loads(rows[1])
    low.update(lamport=1, id="elow")  # the tail is low: [..., 91, 1]
    shard.write_text("\n".join([*rows, json.dumps(big), json.dumps(low)]) + "\n")
    ev = EventLog(repo, "writer").append("session.note", "S1", {"text": "w"})
    assert ev.lamport == big["lamport"] + 1


def test_a_removed_or_unreadable_mark_is_a_full_read(tmp_path):
    repo = _project(tmp_path)
    EventLog(repo, "writer").append("session.note", "S1", {"text": "x"})
    for f in LOCAL_DIR_FILES(repo):
        f.write_text("{not json")
    ev = EventLog(repo, "writer").append("session.note", "S1", {"text": "y"})
    assert ev.lamport == _full(repo)


def test_an_append_in_a_fresh_process_does_not_parse_every_shard(tmp_path, monkeypatch):
    repo = _project(tmp_path, 3000)
    EventLog(repo, "writer").append("session.note", "S1", {"text": "x"})
    from ddflow.infra import log as L

    L.clear_parse_cache()
    reads = []
    real = EventLog._read_shard
    monkeypatch.setattr(
        EventLog, "_read_shard", lambda self, p: (reads.append(p), real(self, p))[1]
    )
    EventLog(repo, "writer").append("session.note", "S1", {"text": "y"})
    assert len(reads) <= 3  # the stamp guard reads for the version stamp; not one per shard
