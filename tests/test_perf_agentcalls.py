"""Agent activity counts (PM-agent-calls): the counter, the hook, the importer port+fake."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ddflow.services.perf.agentcalls import NOT_COLLECTED, FakeImporter, ToolCall, record, rollup


def test_record_appends_one_count_but_never_arguments(tmp_path: Path) -> None:
    record(tmp_path, "hook", ToolCall("agent-1", "lane-a", "ITEM-1", "shell", True, 2.5))
    rows = rollup(tmp_path)
    (row,) = rows
    assert row["tool_class"] == "shell" and row["count"] == 1
    # the arguments never reach the ring: the stored raw line has no prompt text
    ring = tmp_path / ".ddflow" / "local" / "perf" / "agent_calls.jsonl"
    text = ring.read_text("utf-8")
    assert "prompt" not in text and "secret" not in text


def test_a_failed_call_is_counted_not_dropped(tmp_path: Path):
    record(tmp_path, "hook", ToolCall("a", "l", "i", "edit", False))
    (row,) = rollup(tmp_path)
    assert row["count"] == 1 and row["ok"] == 0 and row["failed"] == 1
    assert row["timed"] == 0  # no duration: the -1 sentinel, not a timed run


def test_a_duration_is_timed_separately_from_a_plain_count(tmp_path: Path):
    record(tmp_path, "hook", ToolCall("a", "l", "i", "shell", True, 2.5))
    record(tmp_path, "hook", ToolCall("a", "l", "i", "shell", True))  # no duration
    (row,) = rollup(tmp_path)
    assert row["count"] == 2 and row["timed"] == 1 and row["total_s"] == 2.5


def test_doctor_splits_the_hook_from_a_wired_importer(tmp_path: Path):
    from ddflow.services.perf.agentcalls import doctor

    record(tmp_path, "hook", ToolCall("a", "l", "i", "shell", True, 1.0))
    record(tmp_path, "claude_code_otel", ToolCall("a", "l", "i", "shell", True, 1.0))
    d = {r["source"]: r for r in doctor(tmp_path)}
    assert d["hook"]["count"] == 1 and d["hook"]["in_use"] is True
    assert d["claude_code_otel"]["in_use"] is True  # wired importer's rows are its own
    assert d["session_jsonl"]["unavailable"] is True  # not-wired = unavailable, never zero


def test_the_fake_importer_returns_its_canned_records():
    imp = FakeImporter([ToolCall("a", "l", "i", "read", True, 0.2)])
    recs = imp.call_records("samples.jsonl")
    assert recs and recs[0].tool_class == "read"


def test_the_real_importers_are_named_not_collected():
    assert "claude_code_otel" in NOT_COLLECTED
    assert "session_jsonl" in NOT_COLLECTED


def test_the_hook_is_thin_and_never_records_a_duration_zero(tmp_path: Path):
    from ddflow.services.hooks_perf import record_tool_call

    record_tool_call(tmp_path, "a", "l", "i", "search", True)  # no duration
    (row,) = rollup(tmp_path)
    assert row["count"] == 1  # the missing duration is a count, never a 0-second duration
