"""SU-init-fast: the cached interface snapshot (writer outside mcp.py, stdlib-only reader).

The worker writes `.ddflow/local/mcp-interface.json` on start and when the digest changes; the
shim reads it and answers `initialize`, `server/discover`, `tools/list` and `prompts/list` without
waking the worker. R-su-init-fast-parts: what it serves equals what the live Server serves.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from ddflow.mcpshim import snapshot as S

ROOT = Path(__file__).resolve().parents[1]
GOVERNS = (
    "ddflow/mcpshim/snapshot.py",
    "ddflow/surfaces/ifacesnap.py",
    "ddflow/api/mcp_snapshot.py",
)


def _git(path: Path) -> Path:
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    return path


def sample(**kw) -> dict:
    data = {
        "form": S.FORM,
        "version": "1.2.3",
        "digest": "d" * 64,
        "tier": "all",
        "protocols": ["2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"],
        "modern_protocols": ["2026-07-28"],
        "capabilities": {"tools": {"listChanged": False}},
        "server_info": {"name": "ddflow", "version": "1.2.3", "title": "t"},
        "instructions": "hello",
        "tools": [{"name": "ddflow_x", "description": "d", "inputSchema": {"type": "object"}}],
        "prompts": [{"name": "p", "title": "P", "description": "d", "arguments": []}],
    }
    data.update(kw)
    return data


# ---- the reader is stdlib-only -----------------------------------------------------------


def test_the_reader_imports_only_the_standard_library():
    tree = ast.parse((ROOT / "ddflow/mcpshim/snapshot.py").read_text())
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom):
            assert n.level == 0 and n.module.split(".")[0] in sys.stdlib_module_names, n.module
        elif isinstance(n, ast.Import):
            assert all(a.name.split(".")[0] in sys.stdlib_module_names for a in n.names)


# ---- validate and read --------------------------------------------------------------------


def test_a_well_formed_snapshot_has_no_problems():
    assert S.problems(sample()) == []


@pytest.mark.parametrize(
    "mutate,fragment",
    [
        (lambda d: d.update(form=S.FORM + 1), "form"),
        (lambda d: d.pop("tools"), "tools"),
        (lambda d: d.update(tools="x"), "tools"),
        (lambda d: d.update(protocols=[]), "protocols"),
        (lambda d: d.update(instructions=3), "instructions"),
        (lambda d: d.update(server_info={}), "server_info"),
        (lambda d: d.update(digest=""), "digest"),
        (lambda d: d.update(tools=[{"description": "no name"}]), "tools"),
    ],
)
def test_a_malformed_snapshot_names_what_is_wrong(mutate, fragment):
    data = sample()
    mutate(data)
    assert any(fragment in p for p in S.problems(data))


def test_read_returns_none_for_a_missing_unparseable_or_invalid_file(tmp_path):
    assert S.read(tmp_path) is None
    p = S.path(tmp_path)
    p.parent.mkdir(parents=True)
    p.write_text("{not json")
    assert S.read(tmp_path) is None
    p.write_text(json.dumps({"form": 99}))
    assert S.read(tmp_path) is None
    p.write_text("[1, 2]")
    assert S.read(tmp_path) is None


def test_read_returns_a_valid_snapshot(tmp_path):
    p = S.path(tmp_path)
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps(sample()))
    assert S.read(tmp_path)["digest"] == "d" * 64


def test_the_snapshot_lives_under_the_git_ignored_local_directory(tmp_path):
    assert S.path(tmp_path) == tmp_path / ".ddflow" / "local" / "mcp-interface.json"


# ---- freshness ----------------------------------------------------------------------------


def test_a_snapshot_is_fresh_only_for_the_version_that_wrote_it():
    assert S.fresh(sample(version="1.2.3"), "1.2.3")
    assert not S.fresh(sample(version="1.2.3"), "1.2.4")
    assert not S.fresh(None, "1.2.3")


# ---- the replies ---------------------------------------------------------------------------


def test_initialize_negotiates_a_served_legacy_version_else_the_newest():
    snap = sample()
    assert S.initialize_result(snap, "2025-06-18")["protocolVersion"] == "2025-06-18"
    assert S.initialize_result(snap, "1999-01-01")["protocolVersion"] == "2025-11-25"
    assert S.initialize_result(snap, None)["protocolVersion"] == "2025-11-25"
    r = S.initialize_result(snap, "2025-11-25")
    assert r["capabilities"] == snap["capabilities"] and r["instructions"] == "hello"
    assert r["serverInfo"] == snap["server_info"]


def test_discover_lists_modern_then_legacy_versions():
    r = S.discover_result(sample())
    assert r["supportedVersions"] == [
        "2026-07-28",
        "2025-11-25",
        "2025-06-18",
        "2025-03-26",
        "2024-11-05",
    ]
    assert r["capabilities"] == sample()["capabilities"] and r["instructions"] == "hello"


def test_tools_and_prompts_lists_are_the_stored_ones():
    snap = sample()
    assert S.tools_list_result(snap) == {"tools": snap["tools"]}
    assert S.prompts_list_result(snap) == {"prompts": snap["prompts"]}


def test_the_replies_do_not_share_state_with_the_snapshot():
    snap = sample()
    S.tools_list_result(snap)["tools"].append({"name": "leak"})
    S.initialize_result(snap, "2025-11-25")["capabilities"]["x"] = 1
    assert len(snap["tools"]) == 1 and "x" not in snap["capabilities"]


# ---- the writer (api + surfaces) -----------------------------------------------------------


def _server(repo: Path, tier: str = "all"):
    from ddflow.surfaces.mcp import Server

    srv = Server(repo)
    srv.tier = tier
    return srv


def test_build_matches_the_live_server_replies(tmp_path):
    from ddflow.surfaces import ifacesnap

    srv = _server(_git(tmp_path / "r"))
    snap = ifacesnap.build(srv, instructions="stable text")
    assert S.problems(snap) == []
    live_tools = srv._m_tools_list({"id": 1}, False)["result"]
    live_prompts = srv._m_prompts_list({"id": 1}, False)["result"]
    assert S.tools_list_result(snap) == live_tools
    assert S.prompts_list_result(snap) == live_prompts
    live_init = srv._m_initialize({"id": 1, "params": {"protocolVersion": "2025-06-18"}}, False)[
        "result"
    ]
    mine = S.initialize_result(snap, "2025-06-18")
    for key in ("protocolVersion", "capabilities", "serverInfo"):
        assert mine[key] == live_init[key], key
    assert mine["instructions"] == "stable text"
    live_disc = srv._discover()
    assert S.discover_result(snap)["supportedVersions"] == live_disc["supportedVersions"]
    assert S.discover_result(snap)["capabilities"] == live_disc["capabilities"]


def test_the_digest_in_the_snapshot_is_the_interface_digest(tmp_path):
    from ddflow.surfaces import ifacedigest as D
    from ddflow.surfaces import ifacesnap

    srv = _server(_git(tmp_path / "r"))
    assert ifacesnap.build(srv, instructions="x")["digest"] == D.digest(D.live_snapshot(srv))


def test_each_tier_has_its_own_tools_and_digest(tmp_path):
    from ddflow.surfaces import ifacesnap

    repo = _git(tmp_path / "r")
    core = ifacesnap.build(_server(repo, "core"), instructions="x")
    full = ifacesnap.build(_server(repo, "all"), instructions="x")
    assert core["digest"] != full["digest"] and len(core["tools"]) < len(full["tools"])


def test_write_then_read_round_trips(tmp_path):
    from ddflow.surfaces import ifacesnap

    repo = _git(tmp_path / "r")
    srv = _server(repo)
    assert ifacesnap.write(srv, instructions="x") is True
    got = S.read(repo)
    assert got == ifacesnap.build(srv, instructions="x") | {"written_at": got["written_at"]}
    assert (repo / ".ddflow" / "local" / ".gitignore").read_text().strip().endswith("*")


def test_refresh_rewrites_only_when_the_digest_or_version_changed(tmp_path):
    from ddflow.surfaces import ifacesnap

    repo = _git(tmp_path / "r")
    srv = _server(repo)
    assert ifacesnap.refresh(srv, instructions="x") is True  # nothing there yet
    p = S.path(repo)
    os.utime(p, (1, 1))
    assert ifacesnap.refresh(srv, instructions="x") is False
    assert p.stat().st_mtime == 1, "an unchanged interface must not touch the file"
    srv.tier = "core"
    assert ifacesnap.refresh(srv, instructions="x") is True
    assert S.read(repo)["tier"] == "core"


def test_refresh_rewrites_a_snapshot_another_version_wrote(tmp_path, monkeypatch):
    from ddflow.surfaces import ifacesnap

    repo = _git(tmp_path / "r")
    srv = _server(repo)
    ifacesnap.write(srv, instructions="x")
    stale = json.loads(S.path(repo).read_text())
    stale["version"] = "0.0.1"
    S.path(repo).write_text(json.dumps(stale))
    assert ifacesnap.refresh(srv, instructions="x") is True
    assert S.read(repo)["version"] != "0.0.1"


def test_refresh_rewrites_a_corrupt_snapshot(tmp_path):
    from ddflow.surfaces import ifacesnap

    repo = _git(tmp_path / "r")
    srv = _server(repo)
    ifacesnap.write(srv, instructions="x")
    S.path(repo).write_text("{broken")
    assert ifacesnap.refresh(srv, instructions="x") is True
    assert S.read(repo) is not None


def test_instructions_change_rewrites_even_when_the_digest_does_not(tmp_path):
    from ddflow.surfaces import ifacesnap

    repo = _git(tmp_path / "r")
    srv = _server(repo)
    ifacesnap.write(srv, instructions="one")
    assert ifacesnap.refresh(srv, instructions="two") is True
    assert S.read(repo)["instructions"] == "two"


def test_a_failed_write_leaves_the_previous_snapshot_intact(tmp_path, monkeypatch):
    from ddflow.api import mcp_snapshot
    from ddflow.surfaces import ifacesnap

    repo = _git(tmp_path / "r")
    srv = _server(repo)
    ifacesnap.write(srv, instructions="old")

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(mcp_snapshot.fsio, "replace_text", boom)
    with pytest.raises(OSError):
        ifacesnap.write(srv, instructions="new")
    assert S.read(repo)["instructions"] == "old"


# ---- speed ------------------------------------------------------------------------------------


def test_serving_every_reply_from_a_real_snapshot_takes_milliseconds(tmp_path):
    from ddflow.surfaces import ifacesnap

    repo = _git(tmp_path / "r")
    ifacesnap.write(_server(repo), instructions="x")
    t0 = time.perf_counter()
    snap = S.read(repo)
    S.initialize_result(snap, "2025-11-25")
    S.discover_result(snap)
    S.tools_list_result(snap)
    S.prompts_list_result(snap)
    assert time.perf_counter() - t0 < 0.2


def test_drift_is_empty_for_a_fresh_snapshot_and_names_what_a_stale_one_gets_wrong(tmp_path):
    from ddflow.surfaces import ifacesnap

    srv = _server(_git(tmp_path / "r"))
    snap = ifacesnap.build(srv, instructions="x")
    assert ifacesnap.drift(srv, snap) == []
    snap["tools"] = snap["tools"][:-1]
    snap["capabilities"] = {"tools": {"listChanged": True}}
    snap["prompts"] = []
    assert {"tools/list", "prompts/list", "capabilities"} <= set(ifacesnap.drift(srv, snap))


def test_write_refuses_a_snapshot_that_would_not_reproduce(tmp_path, monkeypatch):
    from ddflow.surfaces import ifacesnap

    srv = _server(_git(tmp_path / "r"))
    real = ifacesnap.build

    def bad(server, *, instructions):
        d = real(server, instructions=instructions)
        d["tools"] = []
        return d

    monkeypatch.setattr(ifacesnap, "build", bad)
    with pytest.raises(ValueError, match="tools/list"):
        ifacesnap.write(srv, instructions="x")
    assert S.read(srv.repo) is None


def test_the_reply_builders_agree_with_mcp_protocol_for_every_served_version():
    from ddflow.surfaces import mcp_protocol as proto

    snap = sample(
        protocols=list(proto.SUPPORTED_PROTOCOLS),
        modern_protocols=list(proto.MODERN_PROTOCOLS),
        capabilities=proto.capabilities(),
        server_info=dict(proto.SERVER_INFO),
    )
    for want in (*proto.SUPPORTED_PROTOCOLS, "1999-01-01", "", None):
        version = proto.legacy_version(want)
        assert S.initialize_result(snap, want) == proto.initialize_result(version, "hello")
    assert S.discover_result(snap) == proto.discover("hello")


def test_write_refuses_a_snapshot_its_own_reader_would_reject(tmp_path, monkeypatch):
    from ddflow.surfaces import ifacesnap

    srv = _server(_git(tmp_path / "r"))
    monkeypatch.setattr(ifacesnap, "running_version", lambda: "")
    with pytest.raises(ValueError, match="version"):
        ifacesnap.write(srv, instructions="x")
    assert S.read(srv.repo) is None


@pytest.mark.parametrize("key", ["protocols", "modern_protocols"])
def test_a_version_list_with_a_non_string_is_not_a_snapshot(key):
    assert any(key in p for p in S.problems(sample(**{key: ["2025-11-25", None]})))
    assert any(key in p for p in S.problems(sample(**{key: [123]})))
