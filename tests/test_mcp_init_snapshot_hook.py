"""SU-init-fast-hook: `initialize` and `server/discover` answer from the cached interface snapshot.

The server writes `.ddflow/local/mcp-interface.json` after the first handshake it rendered (and
whenever the one on disk is not fresh for this version, tier, interface digest and age) and
serves the next handshake from it without rendering the instruction template, so a connection
costs milliseconds. The volatile upgrade notice is not part of the snapshot: a handshake served
from it says the notice on the first tool result instead. Everything here runs in temp projects
and on stdio subprocesses this file spawns.
"""

from __future__ import annotations

import importlib
import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from ddflow.mcpshim import snapshot as S
from ddflow.surfaces import ifacesnap
from ddflow.surfaces.mcp import Server, serve

INS = importlib.import_module("ddflow.surfaces.mcp._instructions")
ROOT = Path(__file__).resolve().parents[1]
SNAP_TEXT = "FROM THE SNAPSHOT"
NOTICE = "UPGRADE-NOTICE-PENDING"


def _repo(tmp_path: Path, config: str = "") -> Path:
    repo = tmp_path / "r"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    if config:
        (repo / ".ddflow").mkdir()
        (repo / ".ddflow" / "config.toml").write_text(config)
    return repo


def _init(srv: Server, version: str = "2025-11-25") -> dict:
    reply = srv.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": version, "clientInfo": {"name": "t", "version": "1"}},
        }
    )
    assert reply is not None
    return reply["result"]


def _discover(srv: Server) -> dict:
    return srv._discover()


@pytest.fixture
def renders(monkeypatch):
    """Counts how often the instruction template is rendered."""
    calls: list[int] = []
    real = INS._instruction_vars

    def counting(*a, **k):
        calls.append(1)
        return real(*a, **k)

    monkeypatch.setattr(INS, "_instruction_vars", counting)
    return calls


@pytest.fixture
def notice(monkeypatch):
    monkeypatch.setattr(INS, "_upgrade_line", lambda repo, agent: f"\n\n{NOTICE}")


def _write_snapshot(repo: Path, text: str = SNAP_TEXT, **edit) -> dict:
    srv = Server(repo)
    ifacesnap.write(srv, instructions=text)
    if edit:
        data = json.loads(S.path(repo).read_text())
        data.update(edit)
        S.path(repo).write_text(json.dumps(data))
    return json.loads(S.path(repo).read_text())


# ---- served from the snapshot ------------------------------------------------------------


def test_initialize_answers_from_a_fresh_snapshot_without_rendering(tmp_path, renders):
    repo = _repo(tmp_path)
    _write_snapshot(repo)
    got = _init(Server(repo))
    assert got["instructions"] == SNAP_TEXT
    assert renders == [], "a fresh snapshot must not cost a render of the template"
    assert got["protocolVersion"] == "2025-11-25"


def test_discover_answers_from_a_fresh_snapshot_without_rendering(tmp_path, renders):
    repo = _repo(tmp_path)
    _write_snapshot(repo)
    got = _discover(Server(repo))
    assert got["instructions"] == SNAP_TEXT and renders == []


def test_the_replies_other_than_instructions_equal_the_live_ones(tmp_path):
    repo = _repo(tmp_path)
    live = _init(Server(repo))
    _write_snapshot(repo)
    snap = _init(Server(repo))
    for key in ("protocolVersion", "capabilities", "serverInfo"):
        assert snap[key] == live[key], key


def test_a_handshake_from_the_snapshot_takes_milliseconds(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _write_snapshot(repo)

    real = INS._instruction_vars

    def slow(*a, **k):
        time.sleep(2)
        return real(*a, **k)

    monkeypatch.setattr(INS, "_instruction_vars", slow)
    srv = Server(repo)
    for answer in (_init, _discover):
        t0 = time.perf_counter()
        answer(srv)
        assert time.perf_counter() - t0 < 0.2, f"{answer.__name__} must answer in under 200 ms"


# ---- when the snapshot is not used -----------------------------------------------------------


@pytest.mark.parametrize(
    "edit",
    [
        {"version": "0.0.1"},
        {"tier": "core-not-this-tier"},
        {"digest": "0" * 64},
        {"written_at": 1.0},  # older than the age cap
        {"form": 99},
    ],
    ids=["version", "tier", "digest", "age", "form"],
)
def test_a_snapshot_that_is_not_fresh_is_not_served(tmp_path, renders, edit):
    repo = _repo(tmp_path)
    _write_snapshot(repo, **edit)
    got = _init(Server(repo))
    assert got["instructions"] != SNAP_TEXT
    assert renders, "the live template is rendered instead"


@pytest.mark.parametrize("raw", ["{not json", "[1,2]", "", '{"form": 1}'])
def test_a_corrupt_snapshot_is_ignored(tmp_path, renders, raw):
    repo = _repo(tmp_path)
    p = S.path(repo)
    p.parent.mkdir(parents=True)
    p.write_text(raw)
    assert _init(Server(repo))["instructions"] != SNAP_TEXT
    assert renders


def test_an_age_cap_of_zero_turns_serving_from_the_snapshot_off(tmp_path, renders):
    repo = _repo(tmp_path, "[mcp]\ninit_snapshot_max_age_s = 0\n")
    _write_snapshot(repo)
    assert _init(Server(repo))["instructions"] != SNAP_TEXT
    assert renders


def test_the_age_cap_is_a_knob_a_project_can_raise(tmp_path, renders):
    repo = _repo(tmp_path, "[mcp]\ninit_snapshot_max_age_s = 100000000000\n")
    _write_snapshot(repo, written_at=1.0)
    assert _init(Server(repo))["instructions"] == SNAP_TEXT and renders == []


def test_a_negative_age_cap_is_refused_by_the_config(tmp_path):
    from ddflow.config import Config

    repo = _repo(tmp_path, "[mcp]\ninit_snapshot_max_age_s = -5\n")
    with pytest.raises(ValueError, match="init_snapshot_max_age_s"):
        Config.load(repo)


# ---- the write after a live handshake -------------------------------------------------------------


def _run_serve(repo: Path, *methods: str) -> list[dict]:
    lines = [
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": i + 1,
                "method": m,
                "params": {"protocolVersion": "2025-11-25", "clientInfo": {"name": "t"}}
                if m == "initialize"
                else {},
            }
        )
        + "\n"
        for i, m in enumerate(methods)
    ]
    out = io.StringIO()
    serve(repo, stdin=io.StringIO("".join(lines)), stdout=out, offload=False)
    return [json.loads(x) for x in out.getvalue().splitlines() if x.strip()]


def test_a_live_handshake_leaves_a_snapshot_the_next_one_is_served_from(tmp_path, renders, notice):
    repo = _repo(tmp_path)
    first = _run_serve(repo, "initialize", "tools/list")[0]["result"]["instructions"]
    assert NOTICE in first, "the live handshake keeps saying the notice, as before"
    snap = S.read(repo)
    assert snap is not None, "the server writes the snapshot once it has answered"
    assert NOTICE not in snap["instructions"], "the volatile notice is not cached"
    assert snap["instructions"] and first.startswith(snap["instructions"])
    before = len(renders)
    second = _run_serve(repo, "initialize")[0]["result"]["instructions"]
    assert second == snap["instructions"] and len(renders) == before


def test_a_stale_snapshot_is_replaced_after_a_live_handshake(tmp_path):
    repo = _repo(tmp_path)
    _write_snapshot(repo, version="0.0.1")
    _run_serve(repo, "initialize")
    got = S.read(repo)
    assert got["version"] != "0.0.1" and got["instructions"] != SNAP_TEXT


def test_a_handshake_served_from_the_snapshot_does_not_rewrite_it(tmp_path):
    repo = _repo(tmp_path)
    _write_snapshot(repo)
    os.utime(S.path(repo), (1, 1))
    _run_serve(repo, "initialize")
    assert S.path(repo).stat().st_mtime == 1


def test_an_unwritable_snapshot_location_never_breaks_the_handshake(tmp_path):
    repo = _repo(tmp_path)
    (repo / ".ddflow").mkdir()
    (repo / ".ddflow" / "local").write_text("a file where the directory should be")
    replies = _run_serve(repo, "initialize", "tools/list")
    assert "instructions" in replies[0]["result"] and "tools" in replies[1]["result"]


# ---- the notice moves to the first tool result ---------------------------------------------------


def _tool_texts(srv: Server) -> list[str]:
    reply = srv.handle(
        {
            "jsonrpc": "2.0",
            "id": 9,
            "method": "tools/call",
            "params": {"name": "ddflow_help", "arguments": {}},
        }
    )
    return [c["text"] for c in reply["result"]["content"]]


def test_the_upgrade_notice_rides_the_first_tool_result_when_the_handshake_was_cached(
    tmp_path, notice
):
    repo = _repo(tmp_path)
    _write_snapshot(repo)
    srv = Server(repo)
    assert NOTICE not in _init(srv)["instructions"]
    assert any(NOTICE in t for t in _tool_texts(srv)), "said on the first tool result"
    assert not any(NOTICE in t for t in _tool_texts(srv)), "and only once"


def test_a_live_handshake_does_not_repeat_the_notice_on_a_tool_result(tmp_path, notice):
    repo = _repo(tmp_path)
    srv = Server(repo)
    assert NOTICE in _init(srv)["instructions"]
    assert not any(NOTICE in t for t in _tool_texts(srv))


def test_a_failing_notice_never_fails_the_first_tool_call(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _write_snapshot(repo)

    def boom(repo, agent):
        raise RuntimeError("no")

    monkeypatch.setattr(INS, "_upgrade_line", boom)
    srv = Server(repo)
    _init(srv)
    assert _tool_texts(srv)


# ---- over a real stdio connection ------------------------------------------------------------------


def _spawn_roundtrip(repo: Path, frames: list[str], timeout: float = 120) -> list[dict]:
    env = {**os.environ, "DDFLOW_REPO": str(repo), "PYTHONPATH": str(ROOT)}
    env.pop("DDFLOW_MCP_OFFLOAD", None)
    proc = subprocess.run(
        [sys.executable, "-m", "ddflow.surfaces.mcp"],
        input="".join(f + "\n" for f in frames),
        capture_output=True,
        text=True,
        cwd=str(repo),
        env=env,
        timeout=timeout,
    )
    return [json.loads(x) for x in proc.stdout.splitlines() if x.strip().startswith("{")]


def _init_frame(i: int = 1) -> str:
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "id": i,
            "method": "initialize",
            "params": {"protocolVersion": "2025-11-25", "clientInfo": {"name": "t"}},
        }
    )


def test_two_stdio_connections_the_second_is_served_from_the_first_ones_snapshot(tmp_path):
    repo = _repo(tmp_path)
    first = _spawn_roundtrip(repo, [_init_frame()])
    assert first and "instructions" in first[0]["result"]
    snap = S.read(repo)
    assert snap is not None, "the first connection wrote the snapshot"
    data = json.loads(S.path(repo).read_text())
    data["instructions"] = "EDITED ON DISK"
    S.path(repo).write_text(json.dumps(data))
    second = _spawn_roundtrip(repo, [_init_frame()])
    assert second[0]["result"]["instructions"] == "EDITED ON DISK"


def test_malformed_frames_do_not_stop_a_snapshot_handshake(tmp_path):
    repo = _repo(tmp_path)
    _write_snapshot(repo)
    out = _spawn_roundtrip(repo, ["{broken", "[1]", _init_frame(7), "null"])
    assert any(r.get("id") == 7 and r["result"]["instructions"] == SNAP_TEXT for r in out), out


def test_a_live_handshake_after_a_cached_one_cancels_the_deferred_notice(tmp_path, notice):
    repo = _repo(tmp_path)
    _write_snapshot(repo)
    srv = Server(repo)
    assert NOTICE not in _init(srv)["instructions"]  # served from the snapshot: notice deferred
    S.path(repo).unlink()  # the next handshake (a second one, or an expired snapshot) is live
    assert NOTICE in _init(srv)["instructions"]
    assert not any(NOTICE in t for t in _tool_texts(srv)), "said once, in the handshake"


@pytest.mark.parametrize("drop", ["instructions", "digest", "tools"])
def test_a_snapshot_missing_a_field_is_rendered_live_not_a_crash(tmp_path, renders, drop):
    repo = _repo(tmp_path)
    _write_snapshot(repo)
    data = json.loads(S.path(repo).read_text())
    del data[drop]
    S.path(repo).write_text(json.dumps(data))
    assert _init(Server(repo))["instructions"] != SNAP_TEXT and renders
