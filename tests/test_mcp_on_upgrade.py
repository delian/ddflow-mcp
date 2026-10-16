"""B3f7d0f7bf3: an MCP server that outlives an upgrade says so, and (opt-in) replaces itself.

The upgrade is simulated the way it happens: the log carries a stamp newer than the code the
server imported, and the installed package (`installed_version`) is newer too."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from test_upgrade_stamp import stamp_newer

from ddflow.services import upgrade_notice as UN
from ddflow.surfaces import mcp, mcp_upgrade
from ddflow.surfaces.mcp import Server

NEWER = "99.0.0"


@pytest.fixture
def stale(repo: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    stamp_newer(repo, NEWER)
    monkeypatch.setattr(UN, "installed_version", lambda: NEWER)
    monkeypatch.delenv(mcp_upgrade.HANDOFF_ENV, raising=False)
    return repo


def _call(srv: Server, name: str, args: dict | None = None) -> dict:
    reply = srv.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": {"as_agent": "me", **(args or {})}},
        }
    )
    return reply["result"]


def _write(srv: Server) -> dict:
    return _call(srv, "ddflow_phase_add", {"id": "P1", "title": "p"})


def test_a_write_on_a_stale_server_says_reconnect_not_upgrade(stale: Path) -> None:
    res = _write(Server(stale))
    assert res["_meta"]["restart_required"] is True and res["_meta"]["exit"] == 3
    text = res["content"][0]["text"]
    assert "restart the MCP server" in text and "is stale, not the install" in text
    assert NEWER in text and "Merge main" not in text and "Upgrade ddflow" not in text


def test_reads_keep_working_on_a_stale_server(stale: Path) -> None:
    res = _call(Server(stale), "ddflow_status")
    assert not res.get("isError") and "restart_required" not in res.get("_meta", {})


def test_a_genuinely_old_install_keeps_the_plain_refusal(
    stale: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The installed package is no newer than the running code: a restart would change nothing,
    so the skew refusal (upgrade ddflow) stays true and untouched."""
    monkeypatch.setattr(UN, "installed_version", lambda: "0.0.1")
    res = _write(Server(stale))
    assert res["_meta"]["exit"] == 3 and "restart_required" not in res["_meta"]
    assert "REFUSED: this project's log has been worked on by ddflow" in res["content"][0]["text"]


def test_off_shows_the_refusal_as_it_is(stale: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DDFLOW_MCP_ON_UPGRADE", "off")
    res = _write(Server(stale))
    assert "restart_required" not in res["_meta"]
    assert res["content"][0]["text"].startswith("REFUSED: this project's log")
    assert not [
        c for c in _call(Server(stale), "ddflow_status")["content"] if "restart" in c["text"]
    ]


def test_notify_never_re_execs(stale: Path) -> None:
    srv = Server(stale)
    _write(srv)
    _call(srv, "ddflow_status")
    assert srv.reexec_pending is False


def test_reexec_flags_the_server_after_a_read_and_says_nothing(
    stale: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DDFLOW_MCP_ON_UPGRADE", "reexec")
    srv = Server(stale)
    res = _call(srv, "ddflow_status")
    assert srv.reexec_pending is True
    assert not [c for c in res["content"] if "restart the server" in c["text"]]


def test_reexec_flags_the_server_on_a_refused_write(
    stale: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DDFLOW_MCP_ON_UPGRADE", "reexec")
    srv = Server(stale)
    res = _write(srv)
    assert srv.reexec_pending is True and res["_meta"]["restart_required"] is True
    assert "retry the call" in res["content"][0]["text"]


def test_a_process_started_by_a_reexec_at_this_version_does_not_loop(
    stale: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ddflow.infra.log import running_version

    monkeypatch.setenv("DDFLOW_MCP_ON_UPGRADE", "reexec")
    monkeypatch.setenv(mcp_upgrade.HANDOFF_ENV, json.dumps({"from": running_version()}))
    srv = Server(stale)
    res = _call(srv, "ddflow_status")
    assert srv.reexec_pending is False, "the exec changed nothing: fall back to telling"
    assert [c for c in res["content"] if "restart the server" in c["text"]]


def test_the_connection_survives_the_exec(stale: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict = {}
    monkeypatch.setattr(os, "execve", lambda path, argv, env: seen.update(argv=argv, env=env))
    srv = Server(stale, agent="dev")
    srv.protocol, srv.client_info = "2025-03-26", {"name": "harness"}
    mcp_upgrade.execute(srv)
    assert seen["argv"][1:3] == ["-m", "ddflow.surfaces.mcp"]
    monkeypatch.setenv(mcp_upgrade.HANDOFF_ENV, seen["env"][mcp_upgrade.HANDOFF_ENV])
    fresh = Server(stale)
    mcp_upgrade.adopt(fresh)
    assert (fresh.protocol, fresh.client_info, fresh.agent) == (
        "2025-03-26",
        {"name": "harness"},
        "dev",
    )
    assert mcp_upgrade.called_from() == srv.called_from


def test_stdin_lines_are_idle_only_when_nothing_is_waiting() -> None:
    r, w = os.pipe()
    try:
        lines = mcp_upgrade.StdinLines(r)
        assert lines.idle()
        os.write(w, b'{"a": 1}\n{"b": 2}\n')
        assert not lines.idle(), "bytes in the pipe: an exec would lose them"
        it = iter(lines)
        assert next(it) == '{"a": 1}\n'
        assert not lines.idle(), "read ahead but not served"
        assert next(it) == '{"b": 2}\n'
        assert lines.idle()
    finally:
        os.close(r)
        os.close(w)


def test_serve_only_replaces_itself_when_quiet(
    stale: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DDFLOW_MCP_ON_UPGRADE", "reexec")
    r, w = os.pipe()
    ran: list[str] = []

    class Stop(Exception):
        pass

    def fake(srv) -> None:
        ran.append("exec")
        raise Stop

    monkeypatch.setattr(mcp_upgrade, "execute", fake)
    monkeypatch.setattr(mcp_upgrade, "stdio_lines", lambda: mcp_upgrade.StdinLines(r))
    call = {
        "jsonrpc": "2.0",
        "method": "tools/call",
        "params": {"name": "ddflow_status", "arguments": {"as_agent": "me"}},
    }
    # Two requests already waiting: the exec must not happen between them.
    os.write(
        w, (json.dumps({**call, "id": 1}) + "\n" + json.dumps({**call, "id": 2}) + "\n").encode()
    )
    out = []

    class Out:
        def write(self, s: str) -> None:
            out.append(json.loads(s))

        def flush(self) -> None:
            pass

    try:
        with pytest.raises(Stop):
            mcp.serve(stale, stdout=Out(), offload=False)
    finally:
        os.close(r)
        os.close(w)
    assert [m["id"] for m in out] == [1, 2], "both waiting requests were answered first"
    assert ran == ["exec"]


def test_a_real_server_replaces_itself_on_the_same_stdio(tmp_path: Path, stale: Path) -> None:
    """End to end over pipes: the second call, sent with no `initialize`, is answered by the
    NEW process (which, being at the same version, falls back to the note: proof it ran)."""
    site = tmp_path / "site"
    site.mkdir()
    (site / "sitecustomize.py").write_text(
        textwrap.dedent(
            f"""
            from ddflow.services import upgrade_notice as un
            un.installed_version = lambda: {NEWER!r}
            """
        )
    )
    (stale / ".ddflow" / "config.toml").write_text('[mcp]\non_upgrade = "reexec"\n')
    root = str(Path(__file__).resolve().parents[1])
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(site), root])}
    proc = subprocess.Popen(
        [sys.executable, "-m", "ddflow.surfaces.mcp", "--repo", str(stale)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=env,
        text=True,
    )
    try:

        def ask(msg: dict) -> dict:
            proc.stdin.write(json.dumps(msg) + "\n")
            proc.stdin.flush()
            return json.loads(proc.stdout.readline())

        init = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-03-26", "clientInfo": {"name": "t"}},
        }
        assert "result" in ask(init)
        call = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "ddflow_status", "arguments": {"as_agent": "me"}},
        }
        first = ask(call)["result"]["content"]
        assert not [c for c in first if "restart the server" in c["text"]]
        second = ask({**call, "id": 3})["result"]["content"]
        assert [c for c in second if "restart the server" in c["text"]], second
    finally:
        proc.stdin.close()
        proc.wait(timeout=30)
