"""The MCP server lifecycle: a `restart` verb (soft reload) and a `kill` verb (hard end),
reflected in the MCP tool schema, at parity with the CLI, resolving to a reboot when the
server is not local (B-mcp-server-lifecycle)."""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from ddflow.surfaces import mcp_lifecycle as LC
from ddflow.surfaces.cli import build_parser
from ddflow.surfaces.mcp import TOOLS

# -- the verbs exist in the MCP schema AND the CLI --------------------------------


def _closing_leaves() -> set[tuple[str, ...]]:
    root = build_parser()
    action = root._subparsers._group_actions[0]
    leaves: set[tuple[str, ...]] = set()

    def walk(parser, prefix):
        subs = [a for a in parser._actions if hasattr(a, "choices") and a.choices]
        subs = [a for a in subs if hasattr(a, "_name_parser_map") or hasattr(a, "add_parser")]
        if not subs:
            leaves.add(tuple(prefix))
            return
        for a in subs:
            for name, sub in a.choices.items():
                walk(sub, (*prefix, name))

    for name, sub in action.choices.items():
        walk(sub, (name,))
    return leaves


def test_restart_and_kill_are_mcp_tools():
    assert "ddflow_restart" in TOOLS
    assert "ddflow_kill" in TOOLS
    for name, verb in (("ddflow_restart", "restart"), ("ddflow_kill", "kill")):
        assert TOOLS[name].get("lifecycle") == verb
        assert len(TOOLS[name]["description"]) >= 60


def test_restart_and_kill_are_cli_verbs():
    leaves = _closing_leaves()
    assert ("restart",) in leaves, "the CLI `ddflow restart` verb must exist"
    assert ("kill",) in leaves, "the CLI `ddflow kill` verb must exist"


# -- the lifecycle resolves by hosting ------------------------------------------


def _env(monkeypatch, value: str | None) -> None:
    if value is None:
        monkeypatch.delenv(LC.HOSTING_ENV, raising=False)
    else:
        monkeypatch.setenv(LC.HOSTING_ENV, value)


def test_hosting_is_declared_by_the_environment(monkeypatch):
    monkeypatch.setenv(LC.HOSTING_ENV, "local")
    assert LC.hosting() == "local"
    monkeypatch.setenv(LC.HOSTING_ENV, "remote")
    assert LC.hosting() == "remote"


def test_restart_re_execs_when_local_and_reboots_when_remote(monkeypatch):
    _env(monkeypatch, "local")
    assert LC.plan("restart") == {"hosting": "local", "action": "reexec"}
    _env(monkeypatch, "remote")
    assert LC.plan("restart") == {"hosting": "remote", "action": "reboot"}


def test_kill_exits_when_local_and_reboots_when_remote(monkeypatch):
    _env(monkeypatch, "local")
    assert LC.plan("kill") == {"hosting": "local", "action": "exit"}
    _env(monkeypatch, "remote")
    assert LC.plan("kill") == {"hosting": "remote", "action": "reboot"}


def test_a_remote_restart_and_kill_are_both_a_reboot(monkeypatch):
    """Item 4: when the server is not local, both verbs resolve to a reboot of the
    server process/container."""
    _env(monkeypatch, "remote")
    assert LC.plan("restart")["action"] == "reboot"
    assert LC.plan("kill")["action"] == "reboot"


def test_plan_refuses_an_unknown_verb():
    with pytest.raises(ValueError, match="unknown lifecycle verb"):
        LC.plan("resume")


def test_the_outcome_names_the_action(monkeypatch):
    _env(monkeypatch, "local")
    out = LC.outcome("restart")
    assert out.exit == 0
    assert out.data["hosting"] == "local" and out.data["action"] == "reexec"
    assert "re-binding its tools" in out.data["message"]


# -- the verbs act on the running server -----------------------------------------


def test_restart_flags_a_local_server_to_re_exec(monkeypatch):
    _env(monkeypatch, "local")
    srv = SimpleNamespace(reexec_pending=False)
    LC.arm(srv, "restart")
    assert srv.reexec_pending is True


def test_kill_flags_the_server_to_end(monkeypatch):
    _env(monkeypatch, "local")
    srv = SimpleNamespace(kill_pending=False)
    LC.arm(srv, "kill")
    assert srv.kill_pending is True


def test_a_remote_restart_ends_the_process_for_its_supervisor(monkeypatch):
    _env(monkeypatch, "remote")
    srv = SimpleNamespace(kill_pending=False)
    LC.arm(srv, "restart")
    assert srv.kill_pending is True


def test_exit_process_never_returns(monkeypatch):
    class _Exited(Exception):
        pass

    def fake_exit(code):
        raise _Exited

    monkeypatch.setattr(os, "_exit", fake_exit)
    with pytest.raises(_Exited):
        LC.exit_process(7)


def test_over_mcp_a_restart_call_arms_the_server(repo, monkeypatch):
    """The whole feature over the wire: the `ddflow_restart` tool both answers its plan
    and flags the connection to replace itself once the reply is out."""
    from ddflow.surfaces.mcp import Server

    _env(monkeypatch, "local")
    srv = Server(repo)
    reply = srv.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "ddflow_restart", "arguments": {"as_agent": "agent-test"}},
        }
    )
    assert reply["result"]["content"]  # an answer, whatever it is
    assert srv.reexec_pending is True
