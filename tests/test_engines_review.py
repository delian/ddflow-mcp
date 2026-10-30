"""Regression tests for the findings of the engine reviews, each with its reproduction."""

from __future__ import annotations

import time

import pytest

from ddflow.services import mcpconfig
from ddflow.services.engines import (
    EngineError,
    Limits,
    emit,
    limit_detect,
    mcp_shape,
    probe,
    render_argv,
    safety,
    stream_map,
    transcript_map,
)

DSH_RULES = [
    {
        "when": {"path": "type", "equals": "session"},
        "emit": "started",
        "fields": {"session": "sessionId"},
    },
    {
        "when": {"path": "type", "equals": "tool_call"},
        "emit": "tool",
        "fields": {"name": "tool", "call": "callId"},
    },
    {
        "when": {"path": "reason.kind", "equals": "error"},
        "emit": "error",
        "fields": {"message": "reason.error.message"},
    },
    {
        "when": {"path": "usage.totalTokens", "exists": True},
        "emit": "usage",
        "fields": {"input": "usage.inputTokens", "output": "usage.outputTokens"},
    },
    {"when": {"path": "reason.kind", "equals": "completed"}, "emit": "settled", "fields": {}},
]
ENTRY = {"command": "ddflow", "args": ["mcp"]}
TSPEC = {
    "path": "{home}/sessions/{session}.jsonl",
    "format": "jsonl",
    "fields": {
        "role": "msg.role",
        "text": "msg.text",
        "model": "model",
        "ts": "ts",
        "usage": "usage.total",
    },
}


# --- findings of the first review, each with its reproduction ----------------------------
def test_unsafe_optional_chains_and_wide_repeats_are_refused():
    for pat in ("(a?){256}b", "a?" * 30 + "a" * 30, "a*a*a*b"):
        with pytest.raises(EngineError):
            safety.safe_regex(pat)
    t = time.monotonic()
    assert safety.search(safety.safe_regex("(ab){3}c?"), "a" * 5000) is None
    assert time.monotonic() - t < 1


def test_hostile_descriptor_values_are_engine_errors_not_crashes():
    with pytest.raises(EngineError):
        limit_detect.parse({"stream_events": [[]]})
    with pytest.raises(EngineError):
        stream_map.parse_rules([{"when": {"path": "a", "equals": "x"}, "emit": []}])
    with pytest.raises(EngineError):
        stream_map.parse_rules([{"when": {"path": "a", "exists": False}, "emit": "tool"}])


def test_mcp_shape_refuses_nan_constants():
    with pytest.raises(EngineError):
        mcp_shape.render_json(
            mcpconfig.SHAPE_MCP_SERVERS, "d", ENTRY, '{"mcpServers": {"o": {"args": [NaN]}}}'
        )


def test_render_argv_shell_check_is_separator_and_case_blind():
    for bad in ("C:\\Windows\\System32\\cmd.exe", "C:/x/PowerShell.exe", "/bin/BASH", ""):
        with pytest.raises(EngineError):
            render_argv.render([bad, "x"], {})


def test_transcript_path_cannot_climb_out_through_a_placeholder():
    s = transcript_map.parse({**TSPEC, "path": "{home}../etc/passwd"})
    with pytest.raises(EngineError):
        transcript_map.resolve_path(s, home="/tmp/")


def test_probe_programs_are_bare_names_and_output_is_read_capped(tmp_path):
    with pytest.raises(EngineError):
        probe.parse([["/tmp/x", "--version"]])
    big = tmp_path / "big"
    big.write_text("#!/bin/sh\nexec yes aaaaaaaaaaaaaaaa\n")
    big.chmod(0o755)
    t = time.monotonic()
    f = probe.run(
        probe.parse([[str(big), "--version"]], allow_paths=True),
        probe_id="b",
        now="d",
        limits=Limits(probe_output_bytes=2000, probe_timeout_s=3),
    )[0]
    assert len(f["output"]) == 2000 and time.monotonic() - t < 5


def test_stream_map_checks_length_before_stripping():
    class Line(str):
        def strip(self, *a):  # the old order called this on a line of any length
            raise AssertionError("strip() before the length check")

    r = stream_map.parse_rules(DSH_RULES)
    assert stream_map.map_line(Line(" " + "x" * 5000), r, Limits(max_line_chars=100)) is None
    spec = transcript_map.parse(TSPEC)
    assert (
        transcript_map.map_lines([Line(" " + "x" * 5000)], spec, Limits(max_line_chars=100)) == []
    )


def test_second_review_findings():
    import itertools

    for bad in (["plain"], 5, None):
        with pytest.raises(EngineError):
            emit.variant_for(bad)
        with pytest.raises(EngineError):
            emit.render(bad, "p", "x", True)
    s = limit_detect.parse({"stream_events": ["limit"]})
    assert (
        limit_detect.detect(s, events=itertools.chain(["limit"], itertools.repeat("tool"))).by
        == "stream_event"
    )
    with pytest.raises(EngineError):
        render_argv.render(["prog", "A" * 9000], {})
    with pytest.raises(EngineError):
        render_argv.render(["prog"], {}, env={1: "FOO"}, environ={})
    for pat in ("(?=a)", "(?=ab)", "(?!a*)b", "(?<=ab)c"):  # lookarounds audit without crashing
        safety.safe_regex(pat)
    with pytest.raises(EngineError):
        safety.safe_regex("^" + "a*" * 98 + "b$")


def test_third_review_findings(tmp_path):
    with pytest.raises(EngineError):
        mcp_shape.render_json(mcpconfig.SHAPE_MCP_SERVERS, "d", ENTRY, '{"o": 1e999}')
    with pytest.raises(EngineError):
        render_argv.render(["cmd.exe.", "/c"], {})
    with pytest.raises(EngineError):
        render_argv.render(["C:\\x\\CMD.EXE ", "/c"], {})
    with pytest.raises(EngineError):  # eight three-way groups between two stars
        safety.safe_regex("a*" + "(a|[a]|[aa])" * 8 + "a*b")
    two = tmp_path / "two"
    two.write_text("#!/bin/sh\necho out; echo err >&2; head -c 100000 /dev/zero | tr '\\0' a\n")
    two.chmod(0o755)
    f = probe.run(
        probe.parse([[str(two), "--version"]], allow_paths=True),
        probe_id="t",
        now="d",
        limits=Limits(probe_output_bytes=10),
    )[0]
    assert len(f["output"]) <= 10


def test_a_failed_spawn_leaks_no_descriptor():
    import os

    def fds():
        return len(os.listdir("/proc/self/fd"))

    spec = probe.parse([["definitely-not-installed-xyz", "--version"]])
    probe.run(spec, probe_id="x", now="d")
    before = fds()
    for _ in range(20):
        with pytest.raises(OSError):
            probe._run_capped(["/definitely/not/here", "--version"], {}, Limits())
    assert fds() == before
    assert safety.search(safety.safe_regex("a"), "a")
    with pytest.raises(EngineError):
        safety.safe_regex("(")
