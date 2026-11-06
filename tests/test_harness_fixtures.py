"""`ddflow harness record` (B-ac-fixtures): capture a real session, scrub it, write fixtures that
`tests/test_harness_conformance.py` replays, and say when a recording has gone stale.

The session here is a fake agent: it fires the capture hooks the scratch project was given and
talks to the real ddflow MCP server through the tap. No model, no agent binary, no network.
"""

from __future__ import annotations

import io
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from harness_conformance import cases as C

from ddflow.api.harness_record import harness_record
from ddflow.infra import proc
from ddflow.services import harness_record as HRec
from ddflow.services import harness_tap as Tap
from ddflow.services import harnessreg as HR
from ddflow.services import hookio

ROOT = Path(__file__).resolve().parents[1]
SESSION = "7d6c0a52-1111-4222-8333-0123456789ab"
PROMPT_ID = "b12ae6be-5f0c-4687-9a33-6681ac070e94"


def _tap(*args: str, stdin: bytes = b"") -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "ddflow.services.harness_tap", *args],
        input=stdin,
        capture_output=True,
        cwd=ROOT,
        timeout=60,
    )


def _prepare(repo: Path, tmp_path: Path, hid: str = "claude") -> Path:
    scratch = tmp_path / "scratch"
    out = harness_record(repo, hid, prepare=str(scratch))
    assert out.exit == 0, out.reason
    return scratch


def _fire(scratch: Path, native: str, payload: dict) -> None:
    """What the agent does for one hook: run every capture command with the JSON on stdin."""
    settings = json.loads((scratch / ".claude" / "settings.json").read_text())
    lines = [
        h["command"]
        for g in settings["hooks"][native]
        for h in g["hooks"]
        if HRec.TAP_MODULE in h["command"]
    ]
    assert len(lines) == 1, native
    subprocess.run(
        lines[0], shell=True, input=json.dumps(payload).encode(), check=True, cwd=scratch
    )


def _session(scratch: Path) -> None:
    """A short claude session, in claude's own field names, with paths of this machine in it."""
    cwd = str(scratch)
    encoded = re.sub(r"[^A-Za-z0-9]", "-", cwd)
    common = {
        "session_id": SESSION,
        "cwd": cwd,
        "transcript_path": f"{Path.home()}/.claude/projects/{encoded}/{SESSION}.jsonl",
    }
    _fire(scratch, "SessionStart", {**common, "source": "startup", "model": "m-1"})
    _fire(scratch, "UserPromptSubmit", {**common, "prompt": "say PONG", "prompt_id": PROMPT_ID})
    tool = {"tool_name": "Bash", "tool_input": {"command": "ls " + cwd}}
    _fire(scratch, "PreToolUse", {**common, **tool})
    _fire(
        scratch,
        "PostToolUse",
        {**common, **tool, "tool_response": {"stdout": "token=" + "a1b2c3d4e5f6a7b8c9d0" * 2}},
    )
    _fire(scratch, "Stop", {**common, "stop_hook_active": False})
    _fire(scratch, "SessionEnd", {**common, "reason": "other"})


def _mcp(scratch: Path) -> None:
    """A client's first three requests, through the entry the scratch project's .mcp.json holds."""
    entry = json.loads((scratch / ".mcp.json").read_text())["mcpServers"]["ddflow"]
    frames = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "fake", "version": "1"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    ]
    p = subprocess.run(
        [entry["command"], *entry["args"]],
        input="".join(json.dumps(f) + "\n" for f in frames).encode(),
        capture_output=True,
        cwd=scratch,
        env={**os.environ, **entry.get("env", {})},
        timeout=120,
    )
    replies = [json.loads(x) for x in p.stdout.splitlines()]
    assert [r["id"] for r in replies] == [1, 2], p.stderr


# --- the tap ---------------------------------------------------------------------------


def test_the_hook_tap_keeps_stdin_verbatim_and_prints_nothing(tmp_path):
    cap = tmp_path / "cap"
    p = _tap("hook", str(cap), "prompt", stdin=b'{"a": 1}')
    assert (p.returncode, p.stdout) == (0, b"")
    [saved] = (cap / "hooks").glob("*-prompt.json")
    assert saved.read_bytes() == b'{"a": 1}'
    assert saved.stat().st_mode & 0o077 == 0, "a raw payload is readable by its owner only"


def test_the_hook_tap_never_fails_the_turn_it_observes(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    p = _tap("hook", str(blocker / "cap"), "prompt", stdin=b"{}")
    assert (p.returncode, p.stdout) == (0, b"")


def test_the_hook_tap_stops_storing_at_its_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(Tap, "MAX_EVENTS", 2)
    cap = tmp_path / "cap"
    for _ in range(4):
        Tap.store_hook(cap, "stop", b"{}")
    assert len(list((cap / "hooks").iterdir())) == 2


def test_the_hook_tap_drains_a_payload_past_its_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(Tap, "MAX_PAYLOAD_BYTES", 4)
    src = io.BytesIO(b"0123456789")
    assert Tap.hook(tmp_path, "stop", src) == 0
    assert src.read() == b"", "the writer's pipe is never closed under it"
    [saved] = (tmp_path / "hooks").iterdir()
    assert saved.read_bytes() == b"0123"


def test_a_stdin_that_cannot_be_read_is_still_exit_zero(tmp_path):
    class Broken(io.BytesIO):
        def read(self, *_a):
            raise OSError("EIO")

    assert Tap.hook(tmp_path, "stop", Broken()) == 0


def test_a_payload_that_cannot_be_stored_is_still_drained(tmp_path, monkeypatch):
    monkeypatch.setattr(Tap, "MAX_PAYLOAD_BYTES", 4)
    blocker = tmp_path / "file"
    blocker.write_text("x")
    src = io.BytesIO(b"0123456789")
    assert Tap.hook(blocker / "cap", "stop", src) == 0
    assert src.read() == b""


def test_a_capture_that_cannot_be_written_still_gets_the_agent_its_server(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    echo = "import sys\nfor l in sys.stdin: print(l.strip(), flush=True)"
    p = _tap("mcp", str(blocker / "cap"), "--", sys.executable, "-c", echo, stdin=b"ping\n")
    assert (p.returncode, p.stdout) == (0, b"ping\n")


def test_the_mcp_tap_passes_bytes_through_and_logs_both_directions(tmp_path):
    cap = tmp_path / "cap"
    echo = "import sys\nfor l in sys.stdin: print(l.strip().upper(), flush=True)"
    p = _tap("mcp", str(cap), "--", sys.executable, "-c", echo, stdin=b"ping\npong\n")
    assert p.stdout == b"PING\nPONG\n"
    rows = [json.loads(x) for x in (cap / "mcp.jsonl").read_text().splitlines()]
    assert sorted((r["d"], r["line"]) for r in rows) == [
        ("in", "ping"),
        ("in", "pong"),
        ("out", "PING"),
        ("out", "PONG"),
    ]


def test_the_mcp_tap_keeps_passing_bytes_once_the_log_is_full(tmp_path, monkeypatch):
    monkeypatch.setattr(Tap, "MAX_MCP_BYTES", 40)
    log = Tap.McpLog(tmp_path)
    log.note("in", b"first\n")
    log.note("in", b"x" * 100 + b"\n")
    assert len((tmp_path / "mcp.jsonl").read_text().splitlines()) == 1, "one huge line is refused"
    again = Tap.McpLog(tmp_path)
    again.note("in", b"second, from a restarted agent\n")
    assert len((tmp_path / "mcp.jsonl").read_text().splitlines()) == 1, "the cap is on the file"


def test_the_tap_stops_a_server_nobody_reads_any_more(tmp_path):
    server = [sys.executable, "-c", "while True: print('x' * 1000, flush=True)"]
    p = subprocess.Popen(
        [sys.executable, "-m", HRec.TAP_MODULE, "mcp", str(tmp_path / "cap"), "--", *server],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        cwd=ROOT,
    )
    p.stdout.readline()
    p.stdout.close()
    assert p.wait(timeout=60) == 0
    p.stdin.close()


def test_a_tap_that_cannot_start_a_server_says_so_by_its_exit():
    assert _tap("mcp", "cap", "no-dashes", "x").returncode == 1
    assert _tap("hook").returncode == 0


# --- prepare ---------------------------------------------------------------------------


def test_prepare_taps_every_mapped_event_and_the_mcp_server(repo, tmp_path):
    scratch = _prepare(repo, tmp_path)
    hooks = json.loads((scratch / ".claude" / "settings.json").read_text())["hooks"]
    for native in HR.get("claude").hooks.events.values():
        assert any(HRec.TAP_MODULE in h["command"] for g in hooks[native] for h in g["hooks"])
    entry = json.loads((scratch / ".mcp.json").read_text())["mcpServers"]["ddflow"]
    assert HRec.TAP_MODULE in entry["args"]
    assert (scratch / HRec.CAPTURE_DIR / HRec.MANIFEST).is_file()
    assert not (scratch / HRec.CAPTURE_DIR / "hooks").exists(), "no session has run"


def test_prepare_keeps_a_config_that_is_not_json_as_text(repo, tmp_path):
    scratch = tmp_path / "scratch"
    out = harness_record(repo, "codex", prepare=str(scratch))
    assert out.exit == 0, out.reason
    assert out.data["mcp_tapped"] is False
    adopted = json.loads((scratch / HRec.CAPTURE_DIR / "adopt.json").read_text())
    assert isinstance(adopted["config"][".codex/config.toml"], str)


@pytest.mark.parametrize("hid", ["aider", "cline"])
def test_an_agent_with_nothing_to_tap_is_unavailable_and_no_scratch_is_made(repo, tmp_path, hid):
    out = harness_record(repo, hid, prepare=str(tmp_path / "scratch"))
    assert out.exit == 2
    assert out.data["status"] == "unavailable"
    assert not (tmp_path / "scratch").exists()


def test_prepare_says_what_a_plugin_style_agent_cannot_have_tapped(repo, tmp_path):
    out = harness_record(repo, "kilo", prepare=str(tmp_path / "scratch"))
    assert out.exit == 0, out.reason
    assert out.data["hooks_tapped"] == []
    assert "plugin" in out.data["hooks_note"]
    assert out.data["mcp_tapped"] is True
    entry = json.loads((tmp_path / "scratch" / ".kilo" / "kilo.json").read_text())["mcp"]["ddflow"]
    assert HRec.TAP_MODULE in entry["command"], "opencode's shape holds one command list"


def test_prepare_on_a_directory_it_cannot_read_is_refused(repo, tmp_path):
    shut = tmp_path / "shut"
    shut.mkdir(mode=0)
    try:
        out = harness_record(repo, "claude", prepare=str(shut))
    finally:
        shut.chmod(0o700)
    if os.geteuid() == 0:
        pytest.skip("root reads every directory")
    assert out.exit == 3
    assert "cannot make" in out.reason


def test_a_line_is_on_disk_before_it_passes(tmp_path):
    log = Tap.McpLog(tmp_path)

    class Sink(io.BytesIO):
        def write(self, data):
            assert (tmp_path / "mcp.jsonl").read_text(), "logged first"
            return super().write(data)

    proc.pump_lines(io.BytesIO(b"ping\n"), Sink(), log.note, "in")


def test_prepare_under_a_file_is_refused(repo, tmp_path):
    (tmp_path / "file").write_text("keep")
    out = harness_record(repo, "claude", prepare=str(tmp_path / "file" / "scratch"))
    assert out.exit == 3
    assert "cannot make" in out.reason


@pytest.mark.parametrize("where", ["full", "inside", "file"])
def test_prepare_refuses_a_directory_that_is_not_a_fresh_scratch(repo, tmp_path, where):
    target = repo / "scratch" if where == "inside" else tmp_path / where
    if where == "full":
        target.mkdir()
        (target / "mine.txt").write_text("keep")
    if where == "file":
        target.write_text("keep")
    out = harness_record(repo, "claude", prepare=str(target))
    assert out.exit == 3, out.reason
    assert not (target / ".ddflow").exists()
    assert where != "file" or target.read_text() == "keep"


# --- record ----------------------------------------------------------------------------


def _recorded(repo, tmp_path, *, write=True):
    scratch = _prepare(repo, tmp_path)
    _session(scratch)
    _mcp(scratch)
    dest = tmp_path / "fixtures"
    out = harness_record(
        repo, "claude", source=str(scratch), write=write, dest=str(dest), version="9.9.9 (Fake)"
    )
    return scratch, dest / "claude", out


def test_a_session_becomes_fixtures_the_normalizer_replays(repo, tmp_path):
    _scratch, fixtures, out = _recorded(repo, tmp_path)
    assert out.exit == 0, out.reason
    events = {p.stem for p in fixtures.glob("*.json")}
    assert events == {"session_start", "prompt", "pre_tool", "post_tool", "stop", "session_end"}
    fx = json.loads((fixtures / "prompt.json").read_text())
    assert fx["_meta"]["recorded"] is True and fx["_meta"]["synthetic"] is False
    assert fx["_meta"]["version"] == "9.9.9 (Fake)"
    assert re.fullmatch(r"\d{4}-\d\d-\d\d", fx["_meta"]["date"])
    assert fx["expect"] == {"session_id": "s-1", "prompt": "say PONG", "cwd": "/w"}
    pre = json.loads((fixtures / "pre_tool.json").read_text())
    assert pre["expect"]["tool"] == "Bash"
    got = hookio.normalize("claude", "prompt", "claude", json.dumps(fx["stdin"]))
    assert (got.session_id, got.prompt) == ("s-1", "say PONG")


def test_a_recording_is_dated_by_its_capture_not_by_a_later_scrub(repo, tmp_path):
    scratch = _prepare(repo, tmp_path)
    _session(scratch)
    path = scratch / HRec.CAPTURE_DIR / HRec.MANIFEST
    path.write_text(json.dumps({**json.loads(path.read_text()), "prepared": "2026-01-02"}))
    dest = tmp_path / "fixtures"
    harness_record(repo, "claude", source=str(scratch), write=True, dest=str(dest), version="1")
    assert json.loads((dest / "claude" / "stop.json").read_text())["_meta"]["date"] == "2026-01-02"


def test_nothing_of_this_machine_reaches_a_fixture(repo, tmp_path):
    scratch, fixtures, _out = _recorded(repo, tmp_path)
    text = "\n".join(p.read_text() for p in sorted(fixtures.rglob("*.json")))
    for raw in (str(scratch), str(Path.home()), SESSION, PROMPT_ID, "a1b2c3d4e5f6a7b8c9d0"):
        assert raw not in text, raw
    assert re.sub(r"[^A-Za-z0-9]", "-", str(scratch)) not in text
    fx = json.loads((fixtures / "session_start.json").read_text())
    assert fx["stdin"]["transcript_path"] == "~/.claude/projects/-w/s-1.jsonl"


def test_the_mcp_pairs_and_the_adopted_files_are_recorded(repo, tmp_path):
    _scratch, fixtures, _out = _recorded(repo, tmp_path)
    pairs = json.loads((fixtures / "session" / "mcp.json").read_text())["pairs"]
    assert [p["method"] for p in pairs] == ["initialize", "tools/list"]
    assert pairs[1]["response"].keys() & {"result", HRec.TRUNCATED}, "kept, or marked as cut"
    adopted = json.loads((fixtures / "session" / "adopt.json").read_text())
    assert ".mcp.json" in adopted["config"]
    assert ".claude/settings.json" in adopted["config"]
    assert "CLAUDE.md" in adopted["wrote"]
    assert HRec.TAP_MODULE not in json.dumps(adopted), "what adopt wrote, before the taps"


def _mcp_log(scratch: Path, reply: dict) -> None:
    rows = [
        {"d": "in", "line": json.dumps({"id": 1, "method": "m", "params": {}})},
        {"d": "out", "line": json.dumps({"id": 1, "result": reply})},
    ]
    log = scratch / HRec.CAPTURE_DIR / Tap.MCP_LOG
    log.write_text("".join(json.dumps(r) + "\n" for r in rows))


def _pairs_of(repo, scratch: Path, dest: Path) -> list[dict]:
    out = harness_record(
        repo, "claude", source=str(scratch), write=True, dest=str(dest), version="1"
    )
    assert out.exit == 0, out.reason
    return json.loads((dest / "claude" / "session" / "mcp.json").read_text())["pairs"]


def test_a_reply_under_the_cap_is_redacted_all_the_way_down(repo, tmp_path):
    scratch = _prepare(repo, tmp_path)
    secret = "token=" + "a1b2c3d4e5f6a7b8c9d0" * 2
    _mcp_log(scratch, {"deep": [{"text": f"in {scratch}: {secret}"}] * 3})
    [pair] = _pairs_of(repo, scratch, tmp_path / "fixtures")
    kept = json.dumps(pair)
    assert "a1b2c3d4e5f6a7b8c9d0" not in kept and str(scratch) not in kept
    assert pair["response"]["result"]["deep"][2]["text"].startswith("in /w: ")
    assert HRec.TRUNCATED not in pair["response"]


def test_a_reply_over_the_cap_says_it_was_cut_and_keeps_nothing_unscrubbed(repo, tmp_path):
    (repo / ".ddflow").mkdir(exist_ok=True)
    assert run_cli(repo, "init")[0] == 0
    assert run_cli(repo, "config", "hooks.fixture_max_reply_bytes", "100", "--local")[0] == 0
    scratch = _prepare(repo, tmp_path)
    _mcp_log(scratch, {"text": f"{scratch} " * 50})
    [pair] = _pairs_of(repo, scratch, tmp_path / "fixtures")
    cut = pair["response"][HRec.TRUNCATED]
    assert cut["cap"] == 100 and cut["bytes"] > 100
    assert "result" not in pair["response"], "a cut reply never reads as a complete one"
    assert pair["response"]["id"] == 1


def test_the_cap_goes_by_size_and_never_by_method():
    pair = {"method": "tools/list", "response": {"id": 1, "result": {"tools": ["a"]}}}
    assert HRec._capped(pair, 1000) == pair
    assert HRec.TRUNCATED in HRec._capped(pair, 5)["response"]
    other = {**pair, "method": "anything"}
    assert HRec.TRUNCATED in HRec._capped(other, 5)["response"]


def test_without_write_it_reports_and_writes_nothing(repo, tmp_path):
    _scratch, fixtures, out = _recorded(repo, tmp_path, write=False)
    assert out.exit == 0
    assert not fixtures.exists()
    assert {r["event"] for r in out.data["fixtures"]} >= {"prompt", "pre_tool"}


def test_a_capture_with_no_session_is_nothing_to_record(repo, tmp_path):
    scratch = _prepare(repo, tmp_path)
    out = harness_record(repo, "claude", source=str(scratch), version="1.0", write=True)
    assert out.exit == 2
    assert "no session" in out.reason


def test_a_capture_of_another_agent_is_refused(repo, tmp_path):
    scratch = _prepare(repo, tmp_path)
    out = harness_record(repo, "gemini", source=str(scratch), version="1.0")
    assert out.exit == 3
    assert "claude" in out.reason


def test_no_version_is_unavailable_not_a_fixture_without_one(repo, tmp_path):
    scratch = _prepare(repo, tmp_path)
    _session(scratch)
    dest = tmp_path / "fixtures"
    out = harness_record(repo, "claude", source=str(scratch), write=True, dest=str(dest))
    assert out.exit == 2
    assert out.data["status"] == "unavailable"
    assert "--agent-version" in out.reason
    assert not dest.exists()


def test_a_payload_that_is_not_json_is_named_and_not_written(repo, tmp_path):
    scratch = _prepare(repo, tmp_path)
    _session(scratch)
    hooks = scratch / HRec.CAPTURE_DIR / "hooks"
    next(hooks.glob("*-stop.json")).write_text("not json")
    dest = tmp_path / "fixtures"
    out = harness_record(
        repo, "claude", source=str(scratch), write=True, dest=str(dest), version="1"
    )
    assert out.exit == 0
    assert out.data["malformed"] == ["stop"]
    assert not (dest / "claude" / "stop.json").exists()


def test_a_leak_the_scrub_missed_writes_nothing_at_all(repo, tmp_path, monkeypatch):
    scratch = _prepare(repo, tmp_path)
    _session(scratch)
    monkeypatch.setattr(HRec.Scrubber, "value", lambda self, v: v)
    dest = tmp_path / "fixtures"
    out = harness_record(
        repo, "claude", source=str(scratch), write=True, dest=str(dest), version="1"
    )
    assert out.exit == 1
    assert "leak" in out.reason
    assert str(scratch) not in out.reason, "the refusal does not print what it refuses"
    assert not dest.exists()


# --- the CLI ----------------------------------------------------------------------------


def test_the_bare_verb_lists_what_is_recorded_and_runs_nothing(repo):
    d = repo / "tests" / "fixtures" / "harness" / "claude"
    d.mkdir(parents=True)
    (d / "prompt.json").write_text(
        json.dumps(
            {
                "_meta": {"recorded": True, "version": "2.1", "date": "2020-01-01"},
                "stdin": {},
            }
        )
    )
    code, out, err = run_cli(repo, "--json", "harness", "record", "claude")
    assert code == 0, err
    [row] = json.loads(out)["fixtures"]
    assert row["event"] == "prompt" and row["status"] == "recorded"
    assert "older than" in row["flags"][0]


def test_the_bare_verb_shows_a_tier_2_agents_checklist(repo):
    d = repo / "docs" / "ddflow" / "agents" / "checklist"
    d.mkdir(parents=True)
    (d / "cursor.md").write_text("Date walked: never\n")
    code, out, err = run_cli(repo, "--json", "harness", "record", "cursor")
    assert code == 0, err
    assert json.loads(out)["checklist"] == "checklist never walked"


def test_an_unknown_agent_is_a_failure(repo):
    code, _out, err = run_cli(repo, "harness", "record", "nope")
    assert code == 1
    assert "nope" in err


def test_prepare_and_from_are_two_steps_not_one(repo, tmp_path):
    code, _out, err = run_cli(
        repo, "harness", "record", "claude", "--prepare", str(tmp_path / "a"), "--from", "b"
    )
    assert code == 1
    assert "--prepare" in err


# --- what the committed recordings owe the conformance suite ------------------------------


def _recorded_fixtures():
    return [x for x in C.all_fixtures() if x[2]["_meta"]["recorded"]]


def test_every_recording_says_its_version_date_and_source():
    assert _recorded_fixtures(), "at least the live claude payloads are committed"
    for hid, event, fx in _recorded_fixtures():
        meta = fx["_meta"]
        assert meta["version"] != "unversioned", (hid, event)
        assert re.fullmatch(r"\d{4}-\d\d-\d\d", meta["date"]), (hid, event)
        assert meta["source"], (hid, event)
        assert meta["synthetic"] is False, (hid, event)


@pytest.mark.parametrize("path", sorted(C.FIXTURES.glob("*/unmapped/*.json")), ids=lambda p: p.stem)
def test_a_live_payload_of_an_event_no_descriptor_maps_yet_still_normalizes(path):
    """Claude's compaction and sub-agent payloads, until B-am-harness-probe maps those events."""
    fx = json.loads(path.read_text())
    assert fx["_meta"]["recorded"] is True
    plan = hookio.plan_for(path.parents[1].name)
    got = hookio.normalize(plan.normalizer, path.stem, plan.harness, json.dumps(fx["stdin"]))
    assert not got.malformed
    assert got.session_id == fx["expect"]["session_id"]


def _sessions():
    return sorted((C.FIXTURES).glob("*/session/adopt.json"))


def _commands(hooks: dict) -> list[str]:
    return [
        h["command"]
        for groups in hooks.values()
        for g in groups
        for h in g.get("hooks", [])
        if "ddflow" in h.get("command", "")
    ]


def test_an_adopted_hook_command_round_trips_to_the_hook_entry_point():
    assert _sessions(), "at least one recorded adoption is committed"
    for path in _sessions():
        hid = path.parents[1].name
        cfg = json.loads(path.read_text())["config"]
        lines = _commands(cfg[HR.get(hid).hooks.file].get("hooks", {}))
        assert lines, hid
        for line in lines:
            got = re.search(r"ddflow hooks run (\w+) --harness (\w+)", line)
            assert got and got.group(1) in HR.CANONICAL_EVENTS and got.group(2) == hid, line
            shlex.split(line, comments=True)


def test_a_recorded_client_handshake_is_served_by_todays_server(repo):
    """A client opens with `initialize` or, on the modern protocol, `server/discover`."""
    from ddflow.surfaces.mcp import Server

    recorded = sorted(C.FIXTURES.glob("*/session/mcp.json"))
    assert recorded, "at least one recorded MCP session is committed"
    for path in recorded:
        hello = json.loads(path.read_text())["pairs"][0]
        assert hello["method"] in ("initialize", "server/discover"), path
        assert "result" in hello["response"], path
        reply = Server(repo).handle(hello["request"])
        assert "result" in reply, (path, reply)
