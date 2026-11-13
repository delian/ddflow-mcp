"""A shard caught half-rewritten by git is waited for, not flagged (split from test_shard_high_water.py)."""

from __future__ import annotations

import json

from shard_high_water_support import (  # noqa: F401
    AGENT,
    _clone_fixture,
    _commit,
    _dd,
    _doctor,
    _event_ids,
    _lines,
    _marks_doc,
    _marks_path,
    _problem_lines,
    _put_legacy,
    _shard,
)


def _caught_short(clone, monkeypatch, restore_on_sleep: int | None, *, during=None):
    """observe() on a shard caught half-rewritten; the file is whole again at the given
    sleep (None = never). Returns (sleeps, lost)."""
    from ddflow.infra.log import EventLog
    from ddflow.services import shardmarks

    _dd(clone, "status")
    log = EventLog(clone, AGENT)
    path = _shard(clone)
    whole = path.read_text()
    rows = whole.splitlines(keepends=True)
    path.write_text("".join(rows[: len(rows) // 2]))  # the half-written moment
    sleeps = []

    def sleep(_s):
        sleeps.append(1)
        if during is not None and (restore_on_sleep is None or len(sleeps) < restore_on_sleep):
            during(path, rows, len(sleeps))
        if restore_on_sleep is not None and len(sleeps) == restore_on_sleep:
            path.write_text(whole)  # it settles

    monkeypatch.setattr(shardmarks.time, "sleep", sleep)
    shardmarks.observe(clone, log)
    return len(sleeps), shardmarks.lost(clone, log)


def test_a_shard_caught_half_rewritten_by_git_is_not_flagged_as_lost(clone, monkeypatch):
    """B-events-uncommitted-shards: a look that lands while another process rewrites the shard
    in place sees half of it; the sticky flag then reported a loss for events still present.
    It settles on the third look here, so a window of one or two looks would not do."""
    n, lost = _caught_short(clone, monkeypatch, restore_on_sleep=3)
    assert n >= 3 and not lost
    assert not any(r.get("shrunk") for r in _marks_doc(clone)["data"].values())


def test_a_shard_that_stays_short_is_still_a_loss_after_the_looks(clone, monkeypatch):
    n, lost = _caught_short(clone, monkeypatch, restore_on_sleep=None)
    assert lost and n >= 4


def test_a_shard_still_growing_during_the_looks_is_waited_for(clone, monkeypatch):
    """The window is progress-aware: a writer still rewriting the file (its size changes at
    every look) does not use up the looks, so a rewrite longer than four pauses still settles."""

    def grow(path, rows, k):
        path.write_text("".join(rows[: len(rows) // 2 + k]))  # a little more each look

    n, lost = _caught_short(clone, monkeypatch, restore_on_sleep=9, during=grow)
    assert n > 4 and not lost  # more looks than a fixed window of four would have made


def test_a_shard_unlinked_for_a_moment_during_the_looks_is_waited_for(clone, monkeypatch):
    def vanish(path, rows, k):
        path.unlink(missing_ok=True)

    n, lost = _caught_short(clone, monkeypatch, restore_on_sleep=3, during=vanish)
    assert n == 3 and not lost


def test_lost_settles_a_shard_it_catches_half_rewritten(clone, monkeypatch):
    """The settle inside lost(): the shard reads short when doctor looks and is whole again a
    moment later; no loss is reported and nothing is flagged."""
    from ddflow.infra.log import EventLog
    from ddflow.services import shardmarks

    _dd(clone, "status")
    log = EventLog(clone, AGENT)
    path = _shard(clone)
    whole = path.read_text()
    rows = whole.splitlines(keepends=True)
    path.write_text("".join(rows[: len(rows) // 2]))
    sleeps = []

    def sleep(_s):
        sleeps.append(1)
        if len(sleeps) == 2:
            path.write_text(whole)

    monkeypatch.setattr(shardmarks.time, "sleep", sleep)
    assert not shardmarks.lost(clone, log) and len(sleeps) >= 2


def test_a_second_short_look_under_the_lock_flags_nothing_when_the_first_read_whole(
    clone, monkeypatch
):
    """The look taken after the marks lock reads short (the shard is being rewritten again)
    while the settled one read whole: neither is believed, and the flag is not set."""
    from ddflow.infra.log import EventLog
    from ddflow.services import shardmarks

    _dd(clone, "status")
    log = EventLog(clone, AGENT)
    path = _shard(clone)
    whole = path.read_text()
    rows = whole.splitlines(keepends=True)
    real = shardmarks._scan
    calls = {"n": 0}

    def scan(p):
        calls["n"] += 1
        if calls["n"] == 1:  # the unlocked look: bigger than the mark, reads whole
            return real(p)
        path.write_text("".join(rows[: len(rows) // 2]))  # the locked look: mid-rewrite
        try:
            return real(p)
        finally:
            path.write_text(whole)

    key = next(iter(_marks_doc(clone)["data"]))
    doc = _marks_doc(clone)
    doc["data"][key]["size"] = 0  # make observe look at the shard
    _marks_path(clone).write_text(json.dumps(doc))
    monkeypatch.setattr(shardmarks, "_scan", scan)
    shardmarks.observe(clone, log)
    monkeypatch.setattr(shardmarks, "_scan", real)
    assert not any(r.get("shrunk") for r in _marks_doc(clone)["data"].values())
    assert not shardmarks.lost(clone, log)
