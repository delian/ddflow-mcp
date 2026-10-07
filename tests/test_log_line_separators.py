"""B5035a55092: an event whose text holds a character `str.splitlines` breaks on is read whole.

`canonical` writes JSON with ensure_ascii=False, so U+2028, U+0085, \\x1c and friends land in
a shard raw. The reader split lines with `splitlines()`, which cut such an event in two:
both halves were unparseable, the event was lost from every fold and doctor called it a
torn append. A line is what the writer terminates: "\\n" alone.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ddflow.infra.log import EventLog

SEPARATORS = [" ", " ", "\x85", "\x1c", "\x1d", "\x1e", "\x0b", "\x0c", "\r"]


@pytest.mark.parametrize("sep", SEPARATORS, ids=[f"U+{ord(s):04X}" for s in SEPARATORS])
def test_an_event_holding_a_unicode_line_break_is_read_whole(tmp_path: Path, sep: str):
    EventLog(tmp_path, "writer").append("session.note", "s1", {"text": f"before{sep}after"})
    EventLog(tmp_path, "writer").append("session.note", "s1", {"text": "next"})
    log = EventLog(tmp_path, "reader")
    texts = [e.data["text"] for e in log.read_all() if e.kind == "session.note"]
    assert texts == [f"before{sep}after", "next"]
    assert log.skipped_lines == 0
    assert log.verify() == []


def test_a_crlf_shard_still_reads(tmp_path: Path):
    EventLog(tmp_path, "writer").append("session.note", "s1", {"text": "one"})
    shard = tmp_path / ".ddflow" / "events" / "writer.jsonl"
    shard.write_bytes(shard.read_bytes().replace(b"\n", b"\r\n"))
    log = EventLog(tmp_path, "reader")
    assert [e.data["text"] for e in log.read_all() if e.kind == "session.note"] == ["one"]
    assert log.skipped_lines == 0
