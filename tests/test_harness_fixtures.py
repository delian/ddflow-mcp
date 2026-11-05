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
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli
from harness_conformance import cases as C
from harness_conformance import report as R

from ddflow.api.harness_record import harness_record
from ddflow.core.redact import Redactor
from ddflow.services import harness_record as HRec
from ddflow.services import harness_tap as Tap
from ddflow.services import harnessreg as HR
from ddflow.services import hookio

ROOT = Path(__file__).resolve().parents[1]
CHECKLISTS = ROOT / "docs" / "ddflow" / "agents" / "checklist"
#: D-agent-compat tier 2: no headless run, so a person walks a checklist.
TIER_2 = ("cursor", "cline", "devin", "continue", "antigravity", "aider")
TODAY = date(2026, 11, 5)
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


def test_prepare_says_what_a_plugin_style_agent_cannot_have_tapped(repo, tmp_path):
    out = harness_record(repo, "kilo", prepare=str(tmp_path / "scratch"))
    assert out.exit == 0, out.reason
    assert out.data["hooks_tapped"] == []
    assert "plugin" in out.data["hooks_note"]
    assert out.data["mcp_tapped"] is True
    entry = json.loads((tmp_path / "scratch" / ".kilo" / "kilo.json").read_text())["mcp"]["ddflow"]
    assert HRec.TAP_MODULE in entry["command"], "opencode's shape holds one command list"


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
    tools = pairs[1]["response"]["result"]["tools"]
    assert "ddflow_brief" in tools, "a tools/list reply is reduced to the names"
    adopted = json.loads((fixtures / "session" / "adopt.json").read_text())
    assert ".mcp.json" in adopted["config"]
    assert ".claude/settings.json" in adopted["config"]
    assert "CLAUDE.md" in adopted["wrote"]
    assert HRec.TAP_MODULE not in json.dumps(adopted), "what adopt wrote, before the taps"


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


# --- the scrub -------------------------------------------------------------------------


def _scrubber(root="/tmp/x.y/z", home="/home/me"):
    r = Redactor("log", secret_patterns=[r"(?i)token[=:]\s*\S+"], hostname="box9", home=home)
    return HRec.Scrubber(root=root, home=home, redactor=r)


def test_the_scrub_replaces_paths_ids_and_secrets_in_keys_and_values():
    s = _scrubber()
    s.session("abc-session")
    got = s.value(
        {
            "/tmp/x.y/z/f": "/home/me/.a/-tmp-x-y-z/abc-session.jsonl",
            "other": [PROMPT_ID, PROMPT_ID, SESSION, "token=hunter2hunter2", "on box9"],
        }
    )
    assert got["/w/f"] == "~/.a/-w/s-1.jsonl"
    first, again, second, secret, host = got["other"]
    assert first == again != second
    assert re.fullmatch(r"0{8}-0{4}-0{4}-0{4}-0{11}1", first)
    assert "hunter2" not in secret
    assert "box9" not in host


def test_the_scrub_replaces_a_home_in_a_directory_name():
    assert _scrubber().value("x/-home-me-.claude/y") == "x/-w-.claude/y"


def test_the_scrub_cuts_a_long_string_and_says_so():
    got = _scrubber().value("x" * (HRec.MAX_STRING + 50))
    assert len(got) < HRec.MAX_STRING + 40
    assert got.endswith("[cut 50 chars]")


def test_a_replacement_inside_a_longer_word_still_scrubs():
    assert _scrubber(root="/tmp/x").value("/tmp/x/y and /tmp/x") == "/w/y and /w"


def test_leaks_name_the_kind_and_never_the_value():
    s = _scrubber()
    s.session("abc-session")
    assert s.leaks({"a": "/home/me/x"}) == ["home"]
    assert s.leaks({"/tmp/x.y/z": 1}) == ["project path"]
    assert s.leaks({"a": "abc-session"}) == ["session id"]
    assert s.leaks({"a": "-home-me-.claude"}) == ["home"]
    assert s.leaks({"a": "~/x"}) == []


def test_a_home_of_slash_and_a_short_session_id_mangle_nothing():
    s = _scrubber(home="/")
    s.session("1")
    assert s.value("a/b 1") == "a/b 1"
    assert s.leaks({"a": "a/b 1"}) == []


def test_a_hostname_inside_a_longer_word_is_no_leak():
    assert _scrubber().leaks({"a": "box99"}) == []
    assert _scrubber().leaks({"a": "on box9 now"}) == ["hostname"]


def test_junk_on_the_wire_does_not_stop_a_recording(tmp_path):
    cap = tmp_path / HRec.CAPTURE_DIR
    cap.mkdir()
    rows = [
        {"d": "in", "line": "not json"},
        {"d": "in", "line": "[1]"},
        {"d": "in", "line": json.dumps({"id": [1], "method": "m"})},
        {"d": "in", "line": json.dumps({"id": 1, "method": "m", "params": [1]})},
        {"d": "out", "line": json.dumps({"id": 1, "result": {}})},
    ]
    text = "".join(json.dumps(r) + "\n" for r in rows) + '{"d": "in", "li'
    (cap / Tap.MCP_LOG).write_text(text)
    assert [p["method"] for p in HRec._pairs(cap)] == ["m"]


# --- staleness -------------------------------------------------------------------------


def _meta(**kw):
    return {"recorded": True, "version": "2.1.318 (Claude Code)", "date": "2026-11-05", **kw}


def test_a_fresh_recording_of_the_installed_major_has_no_flag():
    flags = HRec.fixture_flags(_meta(), today=TODAY, months=6, installed="2.9.0")
    assert flags == []


def test_an_old_recording_is_flagged_by_its_age():
    [flag] = HRec.fixture_flags(_meta(date="2026-04-04"), today=TODAY, months=6)
    assert "older than 6 months" in flag and "2026-04-04" in flag


def test_the_age_boundary_is_whole_months():
    assert HRec.fixture_flags(_meta(date="2026-05-05"), today=TODAY, months=6) == []
    assert HRec.fixture_flags(_meta(date="2026-05-04"), today=TODAY, months=6)


def test_another_major_version_is_flagged():
    [flag] = HRec.fixture_flags(_meta(), today=TODAY, months=6, installed="3.0.1")
    assert "major 2" in flag and "major 3" in flag


def test_a_recording_with_no_date_is_flagged_not_taken_as_fresh():
    [flag] = HRec.fixture_flags({"recorded": True, "version": "1"}, today=TODAY, months=6)
    assert "no date" in flag


def test_a_synthetic_fixture_is_not_aged():
    meta = {"recorded": False, "version": "unversioned"}
    assert HRec.fixture_flags(meta, today=TODAY, months=6, installed="2.0") == []


def test_an_unreadable_fixture_is_a_row_not_a_crash(tmp_path):
    (tmp_path / "prompt.json").write_text("{")
    [row] = HRec.rows(tmp_path, today=TODAY, months=6)
    assert row["status"] == "unreadable"


def test_every_committed_fixture_of_an_agent_is_a_row(tmp_path):
    (tmp_path / "unmapped").mkdir()
    (tmp_path / "unmapped" / "post_compact.json").write_text(
        json.dumps({"_meta": _meta(date="2020-01-01")})
    )
    [row] = HRec.rows(tmp_path, today=TODAY, months=6)
    assert row["event"] == "unmapped/post_compact" and row["flags"]


def test_the_gap_report_carries_the_flags():
    rows = R.rows()
    assert all("flags" in r for r in rows)
    assert " flags |" in R.render().splitlines()[0]


# --- the checklists ---------------------------------------------------------------------


@pytest.mark.parametrize("agent", TIER_2)
def test_every_tier_2_agent_has_a_checklist_with_the_four_steps(agent):
    text = (CHECKLISTS / f"{agent}.md").read_text()
    for heading in ("## Adopt", "## Approve the MCP server", "## Prompt capture", "## Round trip"):
        assert heading in text, (agent, heading)
    assert re.search(r"^Agent version: \S+", text, re.M), agent
    assert re.search(r"^Date walked: \S+", text, re.M), agent


@pytest.mark.parametrize("agent", TIER_2)
def test_a_checklist_states_the_descriptor_facts_it_rests_on(agent):
    h = HR.get(agent)
    text = (CHECKLISTS / f"{agent}.md").read_text()
    if h is None:
        assert "no descriptor" in text, agent
    else:
        assert h.mcp.path in text, agent


def test_a_checklist_never_walked_says_so(tmp_path):
    p = tmp_path / "x.md"
    p.write_text("Agent version: unverified\nDate walked: never\n")
    assert HRec.checklist_status(p, today=TODAY, months=6) == "checklist never walked"


def test_a_walked_checklist_says_its_date_and_goes_stale(tmp_path):
    p = tmp_path / "x.md"
    p.write_text("Agent version: 1.2\nDate walked: 2026-10-01\n")
    assert (
        HRec.checklist_status(p, today=TODAY, months=6)
        == "fixture only, checklist dated 2026-10-01"
    )
    p.write_text("Agent version: 1.2\nDate walked: 2025-10-01\n")
    got = HRec.checklist_status(p, today=TODAY, months=6)
    assert "2025-10-01" in got and "older than 6 months" in got


# --- the CLI ----------------------------------------------------------------------------


def test_the_bare_verb_lists_what_is_recorded_and_runs_nothing(repo):
    d = repo / "tests" / "fixtures" / "harness" / "claude"
    d.mkdir(parents=True)
    (d / "prompt.json").write_text(json.dumps({"_meta": _meta(date="2020-01-01"), "stdin": {}}))
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
