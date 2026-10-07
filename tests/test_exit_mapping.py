"""One exception-to-exit table for the CLI and MCP (bug B5f3a650c40).

The CLI mapped a refusal (`SkewRefused`, `LeaseError`) to exit 3 and an error to exit 1;
MCP turned every Key/Type/ValueError into "bad arguments", so `ReviewerRefused` -- a
ValueError meaning "a person must run this" -- reached an agent as a malformed call, and
a refusal raised anywhere but `SkewRefused` came back as an internal error. Both surfaces
now ask `core.outcome.exit_for`.
"""

from __future__ import annotations

import pytest

from ddflow.core import outcome as O
from ddflow.core.events import SkewRefused
from ddflow.infra.worktree import GitError
from ddflow.services.leases import LeaseError
from ddflow.services.reviewer_trust import ReviewerRefused


@pytest.mark.parametrize(
    ("exc", "code"),
    [
        (ReviewerRefused("a person runs this"), O.REFUSED),
        (LeaseError("held by U9"), O.REFUSED),
        (SkewRefused("newer log"), O.REFUSED),
        (GitError("git failed"), O.FAIL),
        (ValueError("bad"), O.FAIL),
        (KeyError("k"), O.FAIL),
        (KeyboardInterrupt(), 130),
        (RuntimeError("bug"), None),
    ],
)
def test_one_table_names_each_exception_s_exit(exc, code):
    assert O.exit_for(exc) == code


def _call_raising(repo, monkeypatch, exc):
    from ddflow.surfaces import mcp

    def boom(*_a, **_k):
        raise exc

    monkeypatch.setitem(mcp.TOOLS["ddflow_status"], "api", boom)
    return mcp.Server(repo).handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "ddflow_status", "arguments": {}},
        }
    )


def _text(reply) -> str:
    return " ".join(c.get("text", "") for c in reply["result"]["content"])


def test_mcp_reports_a_reviewer_refusal_as_a_refusal_not_bad_arguments(repo, monkeypatch):
    reply = _call_raising(repo, monkeypatch, ReviewerRefused("a person runs this"))
    assert "bad arguments" not in _text(reply), reply
    assert "a person runs this" in _text(reply)
    assert reply["result"]["_meta"]["exit"] == O.REFUSED, reply


def test_mcp_reports_a_lease_refusal_as_a_refusal_not_an_internal_error(repo, monkeypatch):
    reply = _call_raising(repo, monkeypatch, LeaseError("held by U9"))
    assert "result" in reply and "held by U9" in _text(reply), reply
    assert reply["result"]["_meta"]["exit"] == O.REFUSED, reply


def test_mcp_reports_a_git_failure_as_a_failure_not_an_internal_error(repo, monkeypatch):
    reply = _call_raising(repo, monkeypatch, GitError("merge conflict"))
    assert "GitError: merge conflict" in _text(reply) and "bad arguments" not in _text(reply)
    assert reply["result"]["_meta"]["exit"] == O.FAIL and reply["result"]["isError"], reply


def test_the_cli_exits_from_the_same_table(monkeypatch, capsys):
    from ddflow.surfaces import cli

    for exc, code in ((GitError("git failed"), O.FAIL), (LeaseError("held"), O.REFUSED)):

        def boom(*_a, _exc=exc, **_k):
            raise _exc

        monkeypatch.setattr(cli, "Ctx", boom)
        assert cli.main(["status"]) == code
    err = capsys.readouterr().err
    assert "GitError: git failed" in err and "held" in err


def test_a_plain_value_error_is_still_bad_arguments_on_mcp(repo, monkeypatch):
    reply = _call_raising(repo, monkeypatch, ValueError("no such thing"))
    assert "bad arguments: no such thing" in _text(reply)


def test_a_declared_exit_wins_over_the_bad_arguments_reading(repo, monkeypatch):
    class ArgTypeError(TypeError):
        exit_code = O.FAIL

    reply = _call_raising(repo, monkeypatch, ArgTypeError("typed wrong"))
    assert "bad arguments" not in _text(reply) and "ArgTypeError: typed wrong" in _text(reply)
    assert reply["result"]["_meta"]["exit"] == O.FAIL


def test_a_class_named_in_the_table_is_declared_on_mcp_too(repo, monkeypatch):
    """`_EXIT_BY_NAME` is a declaration like `exit_code`: a ValueError subclass named there
    keeps its exit over MCP instead of reading as bad arguments."""

    class Named(ValueError):
        pass

    monkeypatch.setitem(O._EXIT_BY_NAME, f"{Named.__module__}.{Named.__qualname__}", O.REFUSED)
    assert O.declared_exit(Named("x")) == O.REFUSED
    reply = _call_raising(repo, monkeypatch, Named("named refusal"))
    assert "bad arguments" not in _text(reply) and reply["result"]["_meta"]["exit"] == O.REFUSED
