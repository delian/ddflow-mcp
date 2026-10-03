"""`ddflow companions --verify` launches each MCP companion and requires a JSON-RPC answer (B114).

The fixtures are real child processes: a fake MCP server (a script that answers
`initialize` on stdio) and binaries that are NOT one. Nothing here mocks the spawn,
because the whole claim is about what a launched process does.
"""

from __future__ import annotations

import json
import os
import stat
import sys
import textwrap
import time
from pathlib import Path

import pytest

from ddflow.api import setup as S
from ddflow.services import companions as CO

FAKE_SERVER = textwrap.dedent(
    """
    import json, sys
    for line in sys.stdin:
        msg = json.loads(line)
        if msg.get("method") == "initialize":
            print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {
                "protocolVersion": "2025-03-26", "capabilities": {},
                "serverInfo": {"name": "fake-mcp", "version": "9.9"}}}), flush=True)
    """
)

ERROR_SERVER = textwrap.dedent(
    """
    import json, sys
    for line in sys.stdin:
        msg = json.loads(line)
        print(json.dumps({"jsonrpc": "2.0", "id": msg["id"],
                          "error": {"code": -32601, "message": "nope"}}), flush=True)
    """
)

CHATTY_SERVER = textwrap.dedent(
    """
    import json, sys
    print("starting up...", flush=True)
    print(json.dumps({"jsonrpc": "2.0", "method": "notifications/message", "params": {}}), flush=True)
    for line in sys.stdin:
        msg = json.loads(line)
        print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {}}), flush=True)
    """
)

NOISY_EXIT = "import sys; sys.stderr.write('boom: missing token\\n'); sys.exit(3)"


def _companion(cid: str, command: str, args: list[str], **kw) -> CO.Companion:
    return CO.Companion(id=cid, command=command, args=args, kind="mcp", **kw)


def _script(tmp_path: Path, name: str, body: str) -> list[str]:
    p = tmp_path / name
    p.write_text(body, "utf-8")
    return [str(p)]


def test_a_real_mcp_server_is_verified(tmp_path):
    v = CO.verify_one(_companion("fake", sys.executable, _script(tmp_path, "s.py", FAKE_SERVER)))
    assert v.speaks_mcp is True, v.detail
    assert v.server["name"] == "fake-mcp" and v.server["protocolVersion"] == "2025-03-26"
    assert "fake-mcp 9.9" in v.detail


def test_a_jsonrpc_error_still_proves_it_speaks_the_protocol(tmp_path):
    v = CO.verify_one(_companion("err", sys.executable, _script(tmp_path, "e.py", ERROR_SERVER)))
    assert v.speaks_mcp is True


def test_log_lines_and_notifications_before_the_answer_are_skipped(tmp_path):
    v = CO.verify_one(_companion("chat", sys.executable, _script(tmp_path, "c.py", CHATTY_SERVER)))
    assert v.speaks_mcp is True, v.detail


def test_a_binary_that_echoes_its_input_is_not_a_server():
    """`cat` hands the request straight back: it has id 1 and a method, and no result."""
    v = CO.verify_one(_companion("cat", "cat", []))
    assert v.speaks_mcp is False, v.detail
    assert "echoed the request back" in v.detail and v.elapsed_s < 10


def test_a_binary_that_exits_is_not_a_server_and_its_stderr_is_shown(tmp_path):
    v = CO.verify_one(_companion("boom", sys.executable, ["-c", NOISY_EXIT]))
    assert v.speaks_mcp is False
    assert "exited (3)" in v.detail and "boom: missing token" in v.detail


def test_a_missing_command_is_a_fact_not_a_question():
    v = CO.verify_one(_companion("ghost", "definitely-not-a-binary-xyz", []))
    assert v.speaks_mcp is False and "not on PATH" in v.detail


def test_silence_is_could_not_tell_not_not_a_server():
    v = CO.verify_one(_companion("mute", "sleep", ["30"]), timeout_s=1)
    assert v.speaks_mcp is None, v.detail
    assert "within 1s" in v.detail and v.elapsed_s < 10


def test_the_launched_server_does_not_outlive_the_check(tmp_path):
    """A server (and an npx wrapper's children) is stopped, not leaked."""
    pidfile = tmp_path / "pid"
    body = f"import os, time\nopen({str(pidfile)!r}, 'w').write(str(os.getpid()))\ntime.sleep(60)\n"
    CO.verify_one(_companion("leak", sys.executable, _script(tmp_path, "l.py", body)), timeout_s=1)
    pid = int(pidfile.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_an_answering_server_and_a_sigterm_proof_grandchild_do_not_outlive_the_check(tmp_path):
    """The success path tears down too, and a grandchild that ignores SIGTERM (a wrapper
    that forked the real server) is killed rather than leaked."""
    pidfile = tmp_path / "grandchild.pid"
    body = textwrap.dedent(
        f"""
        import json, os, signal, sys, time
        if os.fork() == 0:
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            open({str(pidfile)!r}, "w").write(str(os.getpid()))
            time.sleep(120)
            sys.exit(0)
        for line in sys.stdin:
            msg = json.loads(line)
            print(json.dumps({{"jsonrpc": "2.0", "id": msg["id"], "result": {{}}}}), flush=True)
            time.sleep(120)
        """
    )
    v = CO.verify_one(_companion("wrap", sys.executable, _script(tmp_path, "w.py", body)))
    assert v.speaks_mcp is True, v.detail
    deadline = time.monotonic() + 5
    while not pidfile.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    pid = int(pidfile.read_text())
    for _ in range(100):  # SIGKILL is async: wait for the reap
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_a_newline_free_flood_neither_grows_unbounded_nor_defeats_the_timeout(tmp_path):
    body = (
        "import sys\nwhile True:\n    sys.stdout.buffer.write(b'x' * 65536); sys.stdout.flush()\n"
    )
    v = CO.verify_one(
        _companion("flood", sys.executable, _script(tmp_path, "f.py", body)), timeout_s=2
    )
    assert v.speaks_mcp is None and v.elapsed_s < 15, v.detail


def test_chatty_stderr_is_summarised_by_its_last_line(tmp_path):
    body = "import sys\nfor i in range(50000):\n    sys.stderr.write(f'log line {i}\\n')\nsys.exit(4)\n"
    v = CO.verify_one(_companion("chatty", sys.executable, _script(tmp_path, "c2.py", body)))
    assert v.speaks_mcp is False and "exited (4)" in v.detail and "log line 49999" in v.detail


def test_the_companions_env_reaches_the_launched_server(tmp_path):
    body = textwrap.dedent(
        """
        import json, os, sys
        for line in sys.stdin:
            msg = json.loads(line)
            print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {
                "serverInfo": {"name": os.environ["VERIFY_ME"]}}}), flush=True)
        """
    )
    c = _companion("env", sys.executable, _script(tmp_path, "v.py", body), env={"VERIFY_ME": "yes"})
    assert CO.verify_one(c).server["name"] == "yes"


def test_a_cli_companion_is_never_launched():
    c = CO.Companion(id="x", command="true", kind="cli")
    v = CO.verify_one(c)
    assert v.speaks_mcp is None and "no MCP server" in v.detail


# -- the scan path must never spawn ------------------------------------------------------


def test_scan_and_the_companions_report_never_launch_a_server(repo, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("verification ran on the scan path")

    monkeypatch.setattr(CO, "verify_one", boom)
    monkeypatch.setattr(CO, "verify", boom)
    CO.scan(repo, probe=False)
    CO.scan(repo, probe=True)
    S.companions(repo)
    S.companions(repo, no_probe=True)


# -- the API ------------------------------------------------------------------------------


def _project(repo: Path, tmp_path: Path, **servers: tuple[str, list[str]]) -> None:
    rows = "".join(
        f'[[companion]]\nid = "{cid}"\nkind = "mcp"\ncommand = {json.dumps(cmd)}\n'
        f"args = {json.dumps(args)}\n\n"
        for cid, (cmd, args) in servers.items()
    )
    (repo / ".ddflow").mkdir(exist_ok=True)
    (repo / ".ddflow" / "companions.toml").write_text(rows, "utf-8")
    # Nothing shipped is "installed" on this machine as far as the scan is concerned, so a
    # run without --id would launch only what the project itself registered.
    CO._write_cache(repo, {c.id: (False, "fixed") for c in CO.load(repo)})


def test_api_exit_codes_and_rows(repo, tmp_path):
    good = _script(tmp_path, "g.py", FAKE_SERVER)
    _project(repo, tmp_path, good=(sys.executable, good), bad=("cat", []))
    ok = S.companions_verify(repo, "good")
    assert ok.exit == 0
    row = ok.data["verified"][0]
    assert (
        row["id"] == "good" and row["state"] == "speaks_mcp" and row["server"]["name"] == "fake-mcp"
    )
    both = S.companions_verify(repo, "good,bad")
    assert both.exit == 1 and "bad" in both.reason
    assert {r["id"]: r["state"] for r in both.data["verified"]} == {
        "good": "speaks_mcp",
        "bad": "not_mcp",
    }


def test_api_default_launches_only_what_is_registered_or_installed(repo, tmp_path):
    good = _script(tmp_path, "g.py", FAKE_SERVER)
    marker = tmp_path / "launched"
    spy = f"open({str(marker)!r}, 'w').write('x')"
    _project(repo, tmp_path, good=(sys.executable, good), spy=(sys.executable, ["-c", spy]))
    out = S.companions_verify(repo)
    # Neither is registered in an agent config nor detected, so nothing is launched, and
    # that is said rather than silently treated as success.
    assert out.exit == 2 and out.data["verified"] == []
    assert {s["id"] for s in out.data["skipped"]} >= {"good", "spy"}
    assert not marker.exists()
    # A registered one is launched.
    (repo / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"good": {"command": sys.executable, "args": good}}}), "utf-8"
    )
    out = S.companions_verify(repo)
    assert [r["id"] for r in out.data["verified"]] == ["good"], out.data
    assert out.exit == 0 and not marker.exists()


def test_an_empty_selection_launches_nothing(repo, tmp_path):
    """`ids=[]` means none. It once meant all, so `verify` with nothing chosen started every
    shipped server (an `npx` download each)."""
    _project(repo, tmp_path, spy=(sys.executable, ["-c", "raise SystemExit(0)"]))
    assert CO.verify(repo, []) == []
    assert [v.companion.id for v in CO.verify(repo, ["spy"])] == ["spy"]


def test_api_unknown_id_is_a_failure_that_names_it(repo, tmp_path):
    _project(repo, tmp_path)
    out = S.companions_verify(repo, "nope")
    assert out.exit == 1 and "nope" in out.reason


def test_api_silence_is_exit_2(repo, tmp_path, monkeypatch):
    monkeypatch.setattr(CO, "VERIFY_TIMEOUT_S", 1)
    monkeypatch.setattr(
        CO,
        "verify",
        lambda r, ids=None, timeout_s=1: [
            CO.verify_one(c, timeout_s=1) for c in CO.load(r) if c.id in (ids or [])
        ],
    )
    _project(repo, tmp_path, mute=("sleep", ["30"]))
    out = S.companions_verify(repo, "mute")
    assert out.exit == 2 and out.data["verified"][0]["state"] == "unknown"


def test_stat_exec_bit_is_not_needed_for_scripts(tmp_path):
    """The fake servers are run through the interpreter; guard the helper itself."""
    p = tmp_path / "x"
    p.write_text("")
    assert not (p.stat().st_mode & stat.S_IXUSR)


# -- the real CLI and the MCP tool ---------------------------------------------------------


def _cli(repo, *argv):
    from tests.conftest import run_cli

    return run_cli(repo, *argv)


def test_cli_verify_through_the_real_command(repo, tmp_path):
    good = _script(tmp_path, "g.py", FAKE_SERVER)
    _project(repo, tmp_path, good=(sys.executable, good), bad=("cat", []))
    code, out, err = _cli(repo, "companions", "--verify", "--id", "good")
    assert code == 0, out + err
    assert "[x] good" in out and "fake-mcp 9.9" in out
    code, out, err = _cli(repo, "--json", "companions", "list", "--verify", "--id", "good,bad")
    assert code == 1, out + err
    body = json.loads(out)
    assert {r["id"]: r["state"] for r in body["verified"]} == {
        "good": "speaks_mcp",
        "bad": "not_mcp",
    }
    code, out, err = _cli(repo, "companions", "--verify", "--id", "nope")
    assert code == 1 and "nope" in out + err


def test_cli_companions_without_verify_launches_nothing(repo, tmp_path):
    """The default report (what the MCP handshake calls) must not spawn a server, even
    one that IS registered with an agent."""
    marker = tmp_path / "launched"
    spy = f"open({str(marker)!r}, 'w').write('x')"
    _project(repo, tmp_path, spy=(sys.executable, ["-c", spy]))
    (repo / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"spy": {"command": sys.executable, "args": ["-c", spy]}}}),
        "utf-8",
    )
    for argv in (["companions"], ["companions", "list"], ["companions", "--no-probe"]):
        _cli(repo, *argv)
    assert not marker.exists()
    _code, out, err = _cli(repo, "companions", "--verify")  # opt in: now it launches
    assert marker.exists(), out + err


def test_mcp_tool_verifies_and_the_handshake_does_not_launch(repo, tmp_path):
    from ddflow.surfaces.mcp import Server

    good = _script(tmp_path, "g.py", FAKE_SERVER)
    marker = tmp_path / "launched"
    spy = f"open({str(marker)!r}, 'w').write('x')"
    _project(repo, tmp_path, good=(sys.executable, good), spy=(sys.executable, ["-c", spy]))
    srv = Server(repo)
    init = srv.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert init is not None and not marker.exists()
    reply = srv.handle(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "ddflow_companions_verify", "arguments": {"id": "good"}},
        }
    )
    text = reply["result"]["content"][0]["text"]
    assert json.loads(text)["verified"][0]["state"] == "speaks_mcp", text
    assert not marker.exists()


def test_cli_with_an_unloadable_registry_fails_like_the_plain_report(repo, tmp_path):
    """No per-companion result exists, so there is nothing to call a non-server: it is the
    same failure (exit 1, the reason on stderr) `ddflow companions` gives for that file."""
    (repo / ".ddflow").mkdir(exist_ok=True)
    (repo / ".ddflow" / "companions.toml").write_text('[[companion]]\nid = "x"\nbogus = 1\n')
    plain = _cli(repo, "companions")
    verify = _cli(repo, "companions", "--verify")
    assert plain[0] == 1 and verify[0] == 1
    assert "bogus" in plain[2] and "bogus" in verify[2]
