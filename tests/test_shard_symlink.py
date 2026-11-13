"""A symlinked shard is refused, its target is left alone, and it is not listed
(B143a77ee1d: shard files are committed, so a hostile commit can point one at an
operator-writable file and the next append would write ddflow JSON into it)."""

from __future__ import annotations

from pathlib import Path

import pytest

from ddflow.core.events import Event, utcnow
from ddflow.infra.log import EventLog


def test_a_symlinked_shard_is_refused_and_the_target_is_unchanged(tmp_path: Path):
    events = tmp_path / ".ddflow" / "events"
    events.mkdir(parents=True)
    target = tmp_path / "hold.txt"
    target.write_text("hands off\n")
    (events / "t.jsonl").symlink_to(target)

    log = EventLog(tmp_path, "t")
    with pytest.raises(ValueError):
        log.append("phase.added", "P1", {"title": "Phase one"})

    assert target.read_text() == "hands off\n"


def test_shards_lists_only_real_files_never_a_symlink(tmp_path: Path):
    events = tmp_path / ".ddflow" / "events"
    events.mkdir(parents=True)
    real = events / "a.jsonl"
    real.write_text("\n")
    # A symlink to an EXISTING file: `is_file()`-style filters would keep it, so this
    # proves `shards()` drops it for being a symlink, not for pointing at nothing.
    (tmp_path / "elsewhere.txt").write_text("keep\n")
    (events / "z.jsonl").symlink_to(tmp_path / "elsewhere.txt")

    log = EventLog(tmp_path, "t")
    assert log.shards() == [real]


def test_a_line_rewrite_never_follows_a_symlinked_shard(tmp_path: Path):
    events = tmp_path / ".ddflow" / "events"
    events.mkdir(parents=True)
    target = tmp_path / "hold.txt"
    target.write_text('{"id": "X", "kind": "phase.added"}\n')
    (events / "t.jsonl").symlink_to(target)

    ev = Event(kind="phase.added", subject="P", data={}, agent="t", lamport=1, ts=utcnow())
    ev = Event(**{**ev.__dict__, "id": ev.compute_id()})

    log = EventLog(tmp_path, "t")
    with pytest.raises(KeyError):
        log._rewrite_line("X", ev)

    assert target.read_text() == '{"id": "X", "kind": "phase.added"}\n'
