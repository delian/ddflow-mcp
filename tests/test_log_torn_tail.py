"""B28b3839fe6: a torn event-log tail must not swallow the next event.

A crash mid-append leaves an unterminated fragment at the end of a shard. The append
that follows used to be written straight after it, so the new event shared the
fragment's line, failed to parse with it, and was lost. The writer now terminates the
fragment first (under the lock), and the reader recovers an event that an older writer
already glued onto a fragment.
"""

from __future__ import annotations

import pytest

from ddflow.config import LogConfig
from ddflow.core.events import Event
from ddflow.infra.log import _LINE_START, EventLog, _parse_lines, clear_parse_cache


@pytest.fixture(autouse=True)
def _no_version_stamp(monkeypatch):
    monkeypatch.setattr(EventLog, "stamp", False)


@pytest.fixture(autouse=True)
def _cold_cache():
    clear_parse_cache()
    yield
    clear_parse_cache()


FRAGMENT = b'{"agent":"a1","data":{"t":"half'


def _subjects(log: EventLog) -> list[str]:
    return [e.subject for e in log.read_all()]


@pytest.mark.parametrize("reuse", [True, False])
def test_append_after_torn_tail_keeps_the_new_event(tmp_path, reuse):
    log = EventLog(tmp_path, "a1", log_cfg=LogConfig(reuse_parsed=reuse))
    log.append("task.added", "one")
    assert _subjects(log) == ["one"]  # warm the parse cache on the intact prefix
    with log.shard.open("ab") as fh:
        fh.write(FRAGMENT)  # an append that died mid-write
    log.append("task.added", "after")
    assert _subjects(log) == ["one", "after"]
    # The fragment stays its own (unreadable) line, so doctor still reports it.
    assert log.skipped_lines == 1
    data = log.shard.read_bytes()
    assert data.endswith(b"\n")
    assert FRAGMENT + b"\n" in data
    # And a fresh process (cold cache) reads the same thing.
    clear_parse_cache()
    assert _subjects(EventLog(tmp_path, "a1")) == ["one", "after"]


def test_append_to_intact_shard_adds_no_blank_line(tmp_path):
    log = EventLog(tmp_path, "a1")
    log.append("task.added", "one")
    log.append("task.added", "two")
    assert log.shard.read_bytes().count(b"\n") == 2
    assert b"\n\n" not in log.shard.read_bytes()


def test_reader_recovers_an_event_already_glued_to_a_fragment(tmp_path):
    """Logs written before the fix may already hold `<fragment><event>` on one line."""
    log = EventLog(tmp_path, "a1")
    log.append("task.added", "one")
    glued = EventLog(tmp_path / "other", "a1")
    ev = glued.append("task.added", "glued")
    line = glued.shard.read_bytes()
    with log.shard.open("ab") as fh:
        fh.write(FRAGMENT + line)
    assert ev.subject in _subjects(log)
    assert log.skipped_lines == 1  # the fragment itself is still reported


def test_recovery_refuses_a_suffix_whose_id_does_not_match(tmp_path):
    log = EventLog(tmp_path, "a1")
    ev = log.append("task.added", "x")
    forged = ev.to_json().replace('"subject":"x"', '"subject":"y"').encode()
    events, skipped = _parse_lines(FRAGMENT + forged + b"\n")
    assert events == [] and skipped == 1


def test_non_object_line_is_skipped_not_raised():
    events, skipped = _parse_lines(b"123\n[1,2]\nnull\n")
    assert events == [] and skipped == 3


def test_every_event_line_starts_with_the_recovery_prefix():
    """Recovery looks for `_LINE_START`; a field sorting before `agent` would break it."""
    ev = Event(kind="task.added", subject="s")
    assert ev.to_json().startswith(_LINE_START)
    assert Event(kind="k", subject="", agent="").to_json().startswith(_LINE_START)
