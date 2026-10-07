"""One 'has the log changed?' mark: `EventLog.mark()` and `decide_then_append`.

It replaced `extent()` (sizes only) and `head()` (three totals). Each missed a change the
other saw, so the cases below are the ones the old pair could not both answer.
"""

from __future__ import annotations

import json

import pytest

from ddflow.infra import log as L
from ddflow.infra.log import EventLog


def _line(agent: str, lamport: int, pad: str = "") -> str:
    return json.dumps(
        {"kind": "session.note", "subject": "s", "lamport": lamport, "agent": agent,
         "data": {"p": pad}}
    ) + "\n"  # fmt: skip


def test_the_totals_match_what_head_reported(repo):
    log = EventLog(repo, "agent-a")
    for _ in range(3):
        log.append("session.started", "s1", {})
    (log.dir / "agent-b.jsonl").write_text(_line("agent-b", 99))
    m = log.mark()
    sizes = [p.stat().st_size for p in log.shards()]
    assert (m.count, m.bytes) == (2, sum(sizes))
    assert m.lamport == 99


def test_one_shard_shrinking_while_another_grows_by_the_same_amount_is_seen(repo):
    """`head()` summed the sizes, so these two changes cancelled and the totals matched."""
    a, b = repo / ".ddflow" / "events" / "a.jsonl", repo / ".ddflow" / "events" / "b.jsonl"
    a.parent.mkdir(parents=True, exist_ok=True)
    a.write_text(_line("a", 1, "x" * 40))
    b.write_text(_line("b", 1))
    log = EventLog(repo, "a")
    before = log.mark()
    delta = len(a.read_text()) - len(_line("a", 1))
    a.write_text(_line("a", 1))
    b.write_text(_line("b", 1, "y" * delta))
    after = log.mark()
    assert (after.bytes, after.count, after.lamport) == (before.bytes, before.count, before.lamport)
    assert after != before


def test_a_same_size_append_with_a_new_clock_is_seen(repo):
    """`extent()` compared sizes only: a shard rewritten to the same size with another
    last clock looked unchanged."""
    path = repo / ".ddflow" / "events" / "a.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_line("a", 1))
    log = EventLog(repo, "a")
    before = log.mark()
    path.write_text(_line("a", 2))
    assert path.stat().st_size == before.shards[0][1]
    assert log.mark() != before


def test_a_mark_is_stable_when_nothing_changes(repo):
    log = EventLog(repo, "agent-a")
    log.append("session.started", "s1", {})
    assert log.mark() == log.mark()
    assert log.mark().shards[0][0] == "agent-a.jsonl"


def test_an_empty_log_marks_as_nothing(repo):
    m = EventLog(repo, "agent-a").mark()
    assert (m.count, m.bytes, m.lamport) == (0, 0, 0)


def test_decide_runs_once_when_the_log_is_unchanged_and_again_when_it_moved(repo):
    log = EventLog(repo, "agent-a")
    calls = []

    def quiet():
        calls.append(1)
        return len(calls)

    with log.decide_then_append(quiet) as got:
        assert got == 1
    assert len(calls) == 1

    calls.clear()

    def racing():
        calls.append(1)
        if len(calls) == 1:
            EventLog(repo, "agent-b").append("session.started", "s9", {})
        return len(calls)

    with log.decide_then_append(racing) as got:
        assert got == 2
    assert len(calls) == 2


def test_the_body_runs_under_the_append_lock(repo):
    log = EventLog(repo, "agent-a")
    with log.decide_then_append(lambda: None):
        assert any(L._HELD.values())
    assert not any(L._HELD.values())


def test_the_lock_is_released_when_the_body_raises(repo):
    log = EventLog(repo, "agent-a")
    with pytest.raises(RuntimeError), log.decide_then_append(lambda: None):
        raise RuntimeError("boom")
    assert not any(L._HELD.values())


def test_the_old_pair_is_gone():
    assert not hasattr(EventLog, "extent")
    assert not hasattr(EventLog, "head")


def test_a_shard_vanishing_during_the_mark_is_dropped_not_raised(repo, monkeypatch):
    """A shard can disappear between the glob and the tail read; the mark must differ
    (the safe path) rather than raise out of every poll loop and every claim."""
    log = EventLog(repo, "agent-a")
    log.append("session.started", "s1", {})
    before = log.mark()

    def gone(path):
        raise FileNotFoundError(path)

    monkeypatch.setattr(L, "_last_line", gone)
    after = log.mark()
    assert after != before
    assert after.count == 0


def test_the_stat_only_mark_reads_no_tail_and_still_sees_growth(repo, monkeypatch):
    log = EventLog(repo, "agent-a")
    log.append("session.started", "s1", {})
    before = log.mark(clock=False)

    def boom(path):
        raise AssertionError("a stat-only mark must not read the shard")

    monkeypatch.setattr(L, "_last_line", boom)
    log.append("session.started", "s1", {})
    assert log.mark(clock=False) != before
    assert log.mark(clock=False).lamport == 0


def test_an_unreadable_shard_raises_rather_than_reading_as_unchanged(repo):
    """Only a VANISHED shard is dropped from the mark; one that cannot be read is an
    error (`ddflow` exits 2, could not run), never a mark that compares equal."""
    import os

    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("root reads anything")
    log = EventLog(repo, "agent-a")
    log.append("session.started", "s1", {})
    log.shard.chmod(0)
    try:
        with pytest.raises(PermissionError):
            log.mark()
        assert log.mark(clock=False).count == 1  # a stat needs no read permission
    finally:
        log.shard.chmod(0o644)
