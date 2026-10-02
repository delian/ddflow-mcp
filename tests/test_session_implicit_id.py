"""Every prompt and note carries a session id, even when the caller gives none.

`session prompt`/`session note` (CLI and MCP) resolve the id as: the explicit one, else
the latest OPEN session of this agent, else a new implicit session. Text is never
refused or dropped for want of an id, and redaction still happens before disk.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

SECRET = "sk-abcdefghijklmnop1234567890"


def _events(repo: Path, *kinds: str) -> list[dict]:
    out = []
    for shard in (repo / ".ddflow" / "events").glob("*.jsonl"):
        for line in shard.read_text().splitlines():
            ev = json.loads(line)
            if ev["kind"] in kinds:
                out.append(ev)
    return out


def _init(repo):
    run_cli(repo, "init")


def test_prompt_without_an_id_lands_in_the_latest_open_session(repo):
    _init(repo)
    _c, sid, _e = run_cli(repo, "session", "start")
    sid = sid.strip()
    code, out, err = run_cli(repo, "session", "prompt", "--text", "hello there")
    assert code == 0, err
    (p,) = _events(repo, "session.prompt")
    assert p["subject"] == sid
    assert f"session {sid} (latest)" in out
    code, out, _e = run_cli(repo, "session", "note", "--text", "a dead end")
    assert code == 0
    (n,) = _events(repo, "session.note")
    assert n["subject"] == sid and "(latest)" in out


def test_with_no_open_session_an_implicit_one_is_opened_and_the_text_kept(repo):
    _init(repo)
    code, out, err = run_cli(repo, "session", "prompt", "--text", "first words")
    assert code == 0, err
    assert "(implicit, new)" in out
    (p,) = _events(repo, "session.prompt")
    assert p["subject"] and p["data"]["text"] == "first words"
    (s,) = _events(repo, "session.started")
    assert s["subject"] == p["subject"] and s["data"]["implicit"] is True
    # The next one reuses it.
    run_cli(repo, "session", "note", "--text", "n")
    assert len(_events(repo, "session.started")) == 1


def test_explicit_id_is_unchanged(repo):
    _init(repo)
    run_cli(repo, "session", "start")
    code, out, _e = run_cli(repo, "session", "prompt", "s-mine", "--text", "x")
    assert code == 0
    (p,) = _events(repo, "session.prompt")
    assert p["subject"] == "s-mine"
    assert "(latest)" not in out and "implicit" not in out


def test_an_ended_session_is_never_reused(repo):
    _init(repo)
    _c, sid, _e = run_cli(repo, "session", "start")
    sid = sid.strip()
    run_cli(repo, "session", "end", sid)
    code, out, _e = run_cli(repo, "session", "prompt", "--text", "after the end")
    assert code == 0 and "(implicit, new)" in out
    (p,) = _events(repo, "session.prompt")
    assert p["subject"] != sid


def test_the_most_recent_of_several_open_sessions_wins(repo):
    _init(repo)
    run_cli(repo, "session", "start")
    _c, newer, _e = run_cli(repo, "session", "start")
    run_cli(repo, "session", "prompt", "--text", "b")
    last = _events(repo, "session.prompt")[-1]
    assert last["subject"] == newer.strip()


def test_redaction_still_happens_before_disk_without_an_id(repo):
    _init(repo)
    run_cli(repo, "session", "prompt", "--text", f"key {SECRET}")
    run_cli(repo, "session", "note", "--text", f"key {SECRET}")
    blob = "".join(
        p.read_bytes().decode("utf-8", "replace")
        for p in (repo / ".ddflow").rglob("*")
        if p.is_file()
    )
    assert SECRET not in blob


def test_empty_text_is_still_refused(repo):
    _init(repo)
    code, _o, _e = run_cli(repo, "session", "prompt", "--text", "  ")
    assert code != 0
    assert not _events(repo, "session.started")


def test_the_hook_without_a_harness_id_joins_the_latest_session(repo):
    from test_prompt_autocapture import _hook

    _init(repo)
    _c, sid, _e = run_cli(repo, "session", "start")
    _hook(repo, {"prompt": "no id from the harness"})
    _hook(repo, {"prompt": "again, still none"})
    subjects = {p["subject"] for p in _events(repo, "session.prompt")}
    assert subjects == {sid.strip()}


def test_mcp_session_prompt_and_note_take_no_session(repo):
    from ddflow import api as A

    _init(repo)
    out = A.session_prompt(repo, "", "via mcp")
    assert out.exit == 0 and out.data["how"] == "implicit"
    sid = out.data["session"]
    out2 = A.session_note(repo, "", "note via mcp")
    assert out2.data["session"] == sid and out2.data["how"] == "latest"
    from ddflow.surfaces import mcp

    for tool in ("ddflow_session_prompt", "ddflow_session_note"):
        spec = mcp.TOOLS[tool]["properties"]["session"]
        assert spec[2] is False, "session is optional over MCP"


def _orphans(repo):
    from ddflow.infra.log import EventLog

    log = EventLog(repo, "old-agent")
    log.append("session.started", "s-early", {})
    log.append("session.prompt", "", {"text": "lost prompt", "item": ""})
    log.append("session.note", "", {"text": "lost note", "item": ""})


def test_doctor_notes_orphans_and_adopt_orphans_attaches_them(repo):
    _init(repo)
    _orphans(repo)
    _c, out, _e = run_cli(repo, "doctor")
    assert "no session id" in out
    code, out, err = run_cli(repo, "session", "adopt-orphans")
    assert code == 0, err
    adopted = [e for e in _events(repo, "session.prompt", "session.note") if e["subject"]]
    assert {e["data"]["text"] for e in adopted} == {"lost prompt", "lost note"}
    assert all(e["subject"] for e in adopted)
    # Idempotent, and doctor no longer complains.
    run_cli(repo, "session", "adopt-orphans")
    assert len([e for e in _events(repo, "session.prompt") if e["subject"]]) == 1
    _c, out, _e = run_cli(repo, "doctor")
    assert "no session id" not in out


def test_env_session_id_names_an_open_session_and_the_say_so_is_honest(repo, monkeypatch):
    from ddflow import api as A

    _init(repo)
    _c, first, _e = run_cli(repo, "session", "start")
    run_cli(repo, "session", "start")
    monkeypatch.setenv("DDFLOW_SESSION_ID", first.strip())
    out = A.session_prompt(repo, "", "pinned by env")
    assert out.data["session"] == first.strip() and out.data["how"] == "harness"


def test_prompt_logging_off_opens_no_phantom_session(repo):
    _init(repo)
    cfg = repo / ".ddflow" / "config.toml"
    cfg.write_text(cfg.read_text().replace("[session]", "[session]\nlog_prompts = false", 1))
    code, out, _e = run_cli(repo, "session", "prompt", "--text", "words")
    assert code == 0 and "NOT recorded" in out
    assert not _events(repo, "session.started", "session.prompt")


def test_replay_shows_an_adopted_orphan_once(repo):
    _init(repo)
    _orphans(repo)
    run_cli(repo, "session", "adopt-orphans")
    _c, out, _e = run_cli(repo, "replay")
    assert out.count("lost prompt") == 1


def test_the_hook_opening_an_implicit_session_keeps_its_model_and_tool(repo):
    from test_prompt_autocapture import _hook

    _init(repo)
    _hook(repo, {"prompt": "first", "model": "m-1"})
    (s,) = _events(repo, "session.started")
    assert s["data"]["model"] == "m-1" and s["data"]["tool"] == "hook"
