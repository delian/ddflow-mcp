"""The closed engine set E1..E8 (research driver-architecture 4.2), with hostile inputs."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

from ddflow.services import hookio, mcpconfig
from ddflow.services.engines import (
    ENGINES,
    EngineError,
    Limits,
    emit,
    fieldmap,
    limit_detect,
    mcp_shape,
    probe,
    render_argv,
    safety,
    stream_map,
    transcript_map,
)

FIX = Path(__file__).parent / "fixtures" / "dsh_probe"

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


def test_the_set_is_closed_and_complete():
    assert tuple(ENGINES) == (
        "fieldmap",
        "emit",
        "mcp_shape",
        "render_argv",
        "stream_map",
        "limit_detect",
        "transcript_map",
        "probe",
    )


def test_every_engine_exposes_its_entry_points():
    from ddflow.services.engines import ENTRY_POINTS

    assert set(ENTRY_POINTS) == set(ENGINES)
    for name, fns in ENTRY_POINTS.items():
        assert all(callable(getattr(ENGINES[name], f)) for f in fns), name


# --- E1 fieldmap ------------------------------------------------------------------------
def test_fieldmap_matches_hookio_for_every_shipped_dialect():
    stdin = json.dumps(
        {
            "session_id": "s1",
            "prompt": "hi",
            "tool_name": "Bash",
            "cwd": "/w",
            "workspace_roots": ["/r"],
            "toolCall": {"name": "t"},
        }
    )
    for name, fm in hookio.NORMALIZERS.items():
        p = hookio.normalize(name, "prompt", name, stdin)
        got = fieldmap.extract(fm, json.loads(stdin))
        assert (got["session"], got["prompt"], got["cwd"], got["tool"]) == (
            p.session_id,
            p.prompt or "",
            p.cwd,
            p.tool,
        )


def test_fieldmap_from_table_validates():
    fm = fieldmap.from_table({"session": ["a.b", "c"], "tool": ["toolCall.name"]}, "t")
    assert fm.session == ("a.b", "c") and fm.prompt == ()
    for bad in (
        {"nope": ["a"]},
        {"session": "a"},
        {"session": ["a b"]},
        {"session": ["../x"]},
        {"session": ["a..b"]},
        {"session": ["a." * 40 + "b"]},
    ):
        with pytest.raises(EngineError):
            fieldmap.from_table(bad, "t")


def test_fieldmap_deep_and_nonobject_input_is_empty_not_a_crash():
    fm = fieldmap.from_table({"session": ["a.b.c"]}, "t")
    assert fieldmap.extract(fm, {"a": 5})["session"] == ""
    assert fieldmap.extract(fm, {"a": {"b": ["x"]}})["session"] == ""


# --- E2 emit ----------------------------------------------------------------------------
LEGACY = {  # variant -> the shipped emitter id that prints the same
    "plain": "claude",
    "json_context": "gemini",
    "additional_context": "copilot",
    "additional_context_snake": "cursor",
    "context_modification": "cline",
    "empty_json": "antigravity",
    "silent": "cascade",
}


@pytest.mark.parametrize("variant", sorted(LEGACY))
def test_emit_variants_equal_the_shipped_emitters(variant):
    for inject in (True, False):
        for text in ("ctx", ""):
            assert emit.render(variant, "prompt", text, inject, "BeforeAgent") == hookio.EMITTERS[
                LEGACY[variant]
            ]("prompt", text, inject, "BeforeAgent")
    assert emit.variant_for(LEGACY[variant]) == variant


def test_emit_is_closed():
    assert set(emit.VARIANTS) == set(LEGACY)
    with pytest.raises(EngineError):
        emit.render("rm -rf", "prompt", "x", True, "")
    with pytest.raises(EngineError):
        emit.variant_for("nonesuch")


# --- E3 mcp_shape -----------------------------------------------------------------------
ENTRY = {"command": "ddflow", "args": ["mcp"]}
JSON_SHAPES = [
    mcpconfig.SHAPE_MCP_SERVERS,
    mcpconfig.SHAPE_SERVERS,
    mcpconfig.SHAPE_COPILOT,
    mcpconfig.SHAPE_MCP_DOT_SERVERS,
    mcpconfig.SHAPE_OPENCODE,
]


@pytest.mark.parametrize("shape", JSON_SHAPES)
def test_mcp_shape_is_the_golden_of_mcpconfig(shape):
    assert mcp_shape.entry(shape, ENTRY) == mcpconfig.server_entry_for(shape, ENTRY)
    text = mcp_shape.render_json(shape, "ddflow", ENTRY, '{"keep": 1}')
    data = json.loads(text)
    assert data["keep"] == 1
    assert mcpconfig.get_server(data, shape, "ddflow") == mcpconfig.server_entry_for(shape, ENTRY)
    node = data
    for part in mcp_shape.container_path(shape):
        node = node[part]
    assert "ddflow" in node


def test_mcp_shape_hostile_files():
    for text in ("[1]", '{"mcpServers": null}', '{"mcpServers": ["x"]}'):
        with pytest.raises(EngineError):
            mcp_shape.render_json(mcpconfig.SHAPE_MCP_SERVERS, "ddflow", ENTRY, text)
    with pytest.raises(EngineError):
        mcp_shape.render_json("nope", "ddflow", ENTRY, "")
    assert json.loads(mcp_shape.render_json(mcpconfig.SHAPE_SERVERS, "x", ENTRY, ""))


# --- E4 render_argv ---------------------------------------------------------------------
def test_render_argv_substitutes_whole_elements_without_a_shell():
    r = render_argv.render(
        ["dsh", "--model", "{model}", "--prompt-file", "{prompt_file}", "--cwd", "{dir}"],
        {"model": "m; rm -rf /", "prompt_file": "/p/f", "dir": "/w x"},
    )
    assert r.argv == ("dsh", "--model", "m; rm -rf /", "--prompt-file", "/p/f", "--cwd", "/w x")


def test_render_argv_lints():
    for bad in (
        ["dsh", "{nope}"],
        ["{model}", "x"],
        ["bash", "-c", "{model}"],
        [],
        ["a\0b"],
        ["dsh", "{model"],
        ["dsh", "pre{model}post"],
        ["/usr/bin/sh", "x"],
    ):
        with pytest.raises(EngineError):
            render_argv.render(bad, {"model": "m"})
    with pytest.raises(EngineError):
        render_argv.render(["dsh", "{model}"], {})
    with pytest.raises(EngineError):
        render_argv.render(["dsh", "{model}"], {"model": "a\0b"})
    with pytest.raises(EngineError):
        render_argv.render(["dsh"] + ["x"] * 500, {})


def test_render_argv_secrets_by_env_name_never_by_value():
    r = render_argv.render(
        ["dsh"],
        {},
        env={"DEEPSEEK_API_KEY": "DEEPSEEK_API_KEY", "X": "NOPE"},
        environ={"DEEPSEEK_API_KEY": "sk-secret"},
    )
    assert r.env == {"DEEPSEEK_API_KEY": "sk-secret"} and r.missing == ("NOPE",)
    assert "sk-secret" not in repr(r) and "sk-secret" not in json.dumps(r.describe())
    with pytest.raises(EngineError):
        render_argv.render(["dsh"], {}, env={"bad name": "X"}, environ={})


# --- E5 stream_map ----------------------------------------------------------------------
def _map(name):
    rules = stream_map.parse_rules(DSH_RULES)
    return list(stream_map.map_stream((FIX / name).read_text().splitlines(), rules))


def test_stream_map_on_recorded_dsh_streams():
    ev = [e.event for e in _map("headless_bash_ok.ndjson")]
    assert ev == ["started", "tool", "usage", "usage", "settled"]
    first = _map("headless_bash_ok.ndjson")
    assert first[1].fields == {"name": "bash", "call": "call_1"}
    assert first[2].fields == {"input": 1, "output": 1}
    err = _map("headless_missing_credential.ndjson")
    assert [e.event for e in err] == ["started", "error"]
    assert "no credential" in err[1].fields["message"]


def test_stream_map_hostile_lines():
    rules = stream_map.parse_rules(DSH_RULES)
    lim = Limits(max_line_chars=100)
    big = '{"type":"session","sessionId":"' + "A" * 5000 + '"}'
    lines = [
        "",
        "not json",
        "[1,2]",
        "null",
        "[" * 100000,
        big,
        '{"type":"session","sessionId":{"x":1}}',
    ]
    stream = stream_map.map_stream(lines, rules, limits=lim)
    out = list(stream)
    assert out == [stream_map.Event("started", {})]  # a nested value is not a scalar: dropped
    assert stream.skipped == len(lines) - 1


def test_stream_map_rule_validation():
    for bad in (
        [{"when": {"path": "a"}, "emit": "started"}],
        [{"when": {"path": "a", "equals": "x"}, "emit": "bogus"}],
        [{"when": {"path": "a", "regex": "(a+)+$"}, "emit": "tool"}],
        [{"when": {"path": "a", "equals": "x", "regex": "y"}, "emit": "tool"}],
        [{"when": {"path": "../a", "equals": "x"}, "emit": "tool"}],
        [{"when": {"path": "a", "equals": "x"}, "emit": "tool", "fields": {"n": "a b"}}],
        [{"when": {"path": "a", "equals": "x"}, "emit": "tool"}] * 500,
    ):
        with pytest.raises(EngineError):
            stream_map.parse_rules(bad)
    r = stream_map.parse_rules([{"when": {"path": "t", "regex": "^tool_.*"}, "emit": "tool"}])
    assert next(stream_map.map_stream(['{"t":"tool_call"}'], r)).event == "tool"


# --- ReDoS ------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "pat",
    ["(a+)+$", "(a*)*b", "(a|aa)+$", "(.*)*x", r"(a)\1", "a" * 300, "(?:x+x+)+y", "[", "(a{1,9})+"],
)
def test_unsafe_patterns_are_refused(pat):
    with pytest.raises(EngineError):
        safety.safe_regex(pat)


def test_safe_patterns_and_bounded_input_time():
    rx = safety.safe_regex(r"rate.?limit|usage limit reached (?P<reset>[\w :]+)")
    t = time.monotonic()
    assert (
        safety.search(rx, "x" * 10_000_000 + "rate limit") is None
    )  # tail beyond the cap is not read
    assert time.monotonic() - t < 1
    assert safety.search(rx, "rate limit") is not None


# --- E6 limit_detect --------------------------------------------------------------------
SPEC = {
    "exit_codes": [75],
    "stream_events": ["limit"],
    "messages": [r"usage limit reached.*resets? (?P<reset>at [\w :]+)", r"429"],
    "reset_source": "message",
}


def test_limit_detect_each_path():
    s = limit_detect.parse(SPEC)
    assert limit_detect.detect(s, exit_code=75).by == "exit_code"
    assert limit_detect.detect(s, events=["tool", "limit"]).by == "stream_event"
    hit = limit_detect.detect(s, text="Error: usage limit reached, resets at 5pm UTC")
    assert hit.by == "message" and hit.reset == "at 5pm UTC"
    assert limit_detect.detect(s, text="HTTP 429").by == "message"
    assert limit_detect.detect(s, exit_code=0, events=["tool"], text="all fine") is None


def test_limit_detect_hostile_and_validation():
    s = limit_detect.parse(SPEC)
    t = time.monotonic()
    assert limit_detect.detect(s, text="a" * 5_000_000 + "usage limit reached") is None
    assert time.monotonic() - t < 1
    for bad in (
        {"exit_codes": ["x"]},
        {"messages": ["(a+)+$"]},
        {"nope": 1},
        {"exit_codes": [-1]},
        {"reset_source": "shell"},
    ):
        with pytest.raises(EngineError):
            limit_detect.parse(bad)
    assert limit_detect.detect(limit_detect.parse({}), exit_code=1) is None


# --- E7 transcript_map ------------------------------------------------------------------
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


def test_transcript_map_flat_jsonl_and_hostile():
    s = transcript_map.parse(TSPEC)
    lines = [
        json.dumps(
            {"msg": {"role": "user", "text": "hi"}, "ts": "t1", "model": "m", "usage": {"total": 3}}
        ),
        "garbage",
        "",
        json.dumps({"msg": {"role": "assistant", "text": ["nested"]}}),
        "[" * 50000,
        json.dumps({"msg": {"role": "assistant", "text": "x" * 100000}}),
    ]
    out = transcript_map.map_lines(lines, s, limits=Limits(max_line_chars=1000))
    assert out[0] == transcript_map.Entry("user", "hi", 3, "m", "t1")
    assert len(out) == 2 and out[1].text == ""


def test_transcript_map_paths_and_validation():
    s = transcript_map.parse(TSPEC)
    assert transcript_map.resolve_path(s, home="/h", session="abc-1") == "/h/sessions/abc-1.jsonl"
    for sess in ("../../etc/passwd", "a/b", "", "a\0b", ".."):
        with pytest.raises(EngineError):
            transcript_map.resolve_path(s, home="/h", session=sess)
    with pytest.raises(EngineError):
        transcript_map.resolve_path(s, home="rel", session="a")
    for bad in (
        {**TSPEC, "format": "json"},
        {**TSPEC, "path": "{nope}/x"},
        {**TSPEC, "path": "/a/../b"},
        {**TSPEC, "fields": {"bogus": "a"}},
        {"format": "jsonl"},
    ):
        with pytest.raises(EngineError):
            transcript_map.parse(bad)


# --- E8 probe ---------------------------------------------------------------------------
def test_probe_runs_declared_safe_argv_and_stores_a_fact():
    spec = probe.parse([[sys.executable, "--version"]], allow_paths=True)
    facts = probe.run(spec, probe_id="py", now="2026-10-30")
    f = facts[0]
    assert (
        f["status"] == "verified" and f["source"] == "probe:py" and f["retrieved"] == "2026-10-30"
    )
    assert f["tool_version"].startswith("3.") and f["exit_code"] == 0


def test_probe_refuses_what_is_not_declared_safe():
    for bad in (
        [["rm", "-rf", "/"]],
        [[sys.executable, "-c", "print(1)"]],
        [[sys.executable, "--version; id"]],
        [["sh", "--version"]],
        [[]],
        [["dsh", "--version", "--", "x"]],
        "dsh --version",
    ):
        with pytest.raises(EngineError):
            probe.parse(bad)


def test_probe_missing_binary_is_unavailable_and_timeouts_are_bounded(tmp_path):
    spec = probe.parse([["definitely-not-installed-xyz", "--version"]])
    f = probe.run(spec, probe_id="x", now="2026-10-30")[0]
    assert f["status"] == "unavailable" and "tool_version" not in f
    slow = tmp_path / "slow"
    slow.write_text("#!/bin/sh\nsleep 5\n")
    slow.chmod(0o755)
    spec = probe.parse([[str(slow), "--version"]], allow_paths=True)
    t = time.monotonic()
    f = probe.run(spec, probe_id="s", now="d", limits=Limits(probe_timeout_s=0.3))[0]
    assert f["status"] == "unavailable" and f["reason"] == "timeout" and time.monotonic() - t < 3


def test_probe_output_is_capped_and_env_is_scrubbed(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-leak")
    s = tmp_path / "tool"
    s.write_text(
        '#!/bin/sh\nprintf "v1.2.3 key=%s\\n" "$DEEPSEEK_API_KEY"; head -c 300000 /dev/zero | tr "\\0" a\n'
    )
    s.chmod(0o755)
    f = probe.run(
        probe.parse([[str(s), "--version"]], allow_paths=True),
        probe_id="t",
        now="d",
        limits=Limits(probe_output_bytes=1000),
    )[0]
    assert (
        f["tool_version"] == "1.2.3" and len(f["output"]) <= 1000 and "sk-leak" not in f["output"]
    )


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
