"""The `approvals` commands, declared once (RA-cli). CLI-ONLY: answering an approval is a
person's act and no tool carries it; an agent asks through its refusals and reads with
`list`/`show` (a `ddflow_session` action will carry those later, RA-mcp)."""

from __future__ import annotations

from ..registry import Command, Param

_DIGEST = Param(
    "digest",
    default="",
    cli_help="the first characters of the digest `ddflow approvals show` printed (a read-receipt that you looked at this request, not a secret)",
)

COMMANDS: tuple[Command, ...] = (
    Command(
        path=("approvals", "list"),
        summary="the approval requests waiting for a person, and the approved ones not yet spent",
        tool="",
        reason="CLI-only with `approvals approve`: the operator's surface",
        params=(Param("all", type="boolean", default=False, cli_help="every request, any state"),),
    ),
    Command(
        path=("approvals", "show"),
        summary="one request as ddflow rendered it for the person, with its digest and status",
        tool="",
        reason="CLI-only with `approvals approve`: the operator's surface",
        params=(Param("id", required=True, positional=True, cli_help="the request id"),),
    ),
    Command(
        path=("approvals", "request"),
        summary="an agent files an approval request for a gated command (a refusal does it too)",
        tool="",
        reason="CLI-only: a refusal files the request itself; this is the by-hand form",
        params=(
            Param("verb", required=True, positional=True, cli_help="the gated verb"),
            Param("item", default="", cli_help="the item the command acts on"),
            Param("target", default="", cli_help="the gate or other target"),
            Param("reason", default="", cli_help="why (shown to the person as untrusted text)"),
        ),
    ),
    Command(
        path=("approvals", "approve"),
        summary="a PERSON approves one request, once (refused under an agent identity)",
        tool="",
        reason="a person's act: refused for every agent identity, so no tool carries it",
        params=(
            Param("id", required=True, positional=True, cli_help="the request id"),
            _DIGEST,
        ),
    ),
    Command(
        path=("approvals", "deny"),
        summary="a PERSON denies one request (refused under an agent identity)",
        tool="",
        reason="a person's act: refused for every agent identity, so no tool carries it",
        params=(
            Param("id", required=True, positional=True, cli_help="the request id"),
            _DIGEST,
        ),
    ),
    Command(
        path=("approvals", "revoke"),
        summary="a PERSON takes back an approval that is not spent yet",
        tool="",
        reason="a person's act: refused for every agent identity, so no tool carries it",
        params=(Param("id", required=True, positional=True, cli_help="the request id"),),
    ),
    Command(
        path=("approvals", "withdraw"),
        summary="the asker takes back its own request while it waits",
        tool="",
        reason="CLI-only with the rest of `approvals`",
        params=(Param("id", required=True, positional=True, cli_help="the request id"),),
    ),
)
