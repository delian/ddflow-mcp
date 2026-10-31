"""The `approvals` commands, declared once (RA-cli), and their MCP face (RA-mcp, D-RA-6).

The CLI carries every verb. Over MCP there is NO new tool: three actions of `ddflow_session`
(`ask` files a request and returns its handle at once, `asks` lists the caller's OWN asks,
`withdraw` takes back the caller's own), and the optional `approval` parameter the gated
actions already carry (complete, gate skip, claim, review approve ...). Answering (approve,
deny, revoke, attest) is a person's act and no tool carries it: the answer verbs are not
declared here with a tool, and the API an MCP call reaches has no way to record one."""

from __future__ import annotations

import importlib

from ..registry import Command, Param


def _api():
    """`api.approvals` imported when a call is made, so the tool table stays api-free."""
    return importlib.import_module("ddflow.api.approvals")


def _own(repo, agent):
    """The rows of the caller's own asks (never another agent's), with the id they carry."""
    from ...api._base import _load

    log, _cfg, _st = _load(repo, agent)
    out = _api().approvals_list(repo, all_rows=True, agent=agent)
    rows = [r for r in out.data.get("approvals", []) if r["agent"] == log.agent_id]
    return log.agent_id, rows


def _asks(repo, a, agent):
    from ...core import outcome as O

    who, rows = _own(repo, agent)
    if not a.get("all"):
        rows = [r for r in rows if r["status"].split(",")[0] in ("pending", "approved")]
    text = "\n".join(f"{r['id']}  {r['status']:<14} {r['verb']}" for r in rows) or "no asks"
    return O.ok("approvals.asks", agent=who, asks=rows, text=text)


def _withdraw(repo, a, agent):
    from ...core import outcome as O

    ask_id = a.get("id", "") or ""
    who, rows = _own(repo, agent)
    if ask_id not in {r["id"] for r in rows}:  # asker only, whatever the engine would allow
        return O.refused("approvals.withdraw", f"{ask_id} is not one of {who}'s own asks")
    return _api().approvals_withdraw(repo, ask_id, agent=agent)


_MCP = (
    Command(
        path=(),
        tool="ddflow_session_ask",
        family="ddflow_session",
        action="ask",
        tier="all",
        description="File an approval request for a gated command (complete --force, gate skip, "
        "claim --force ...); returns its id at once and never waits. A person answers out of "
        "band; then pass `approval=<id>` on that exact command. Nothing here can answer.",
        call=lambda repo, a, agent: _api().approvals_request(
            repo,
            a.get("verb", "") or "",
            item=a.get("item", "") or "",
            target=a.get("target", "") or "",
            reason=a.get("reason", "") or "",
            agent=agent,
        ),
        payload=("id", "new", "digest"),
        reason="MCP face of `approvals request`, folded into ddflow_session (D-RA-6)",
        params=(
            Param("verb", help="The gated verb.", required=True),
            Param("item", help="Item it acts on.", default=""),
            Param("target", help="Gate or other target.", default=""),
            Param("reason", help="Why (shown to the person as untrusted).", default=""),
        ),
    ),
    Command(
        path=(),
        tool="ddflow_session_asks",
        family="ddflow_session",
        action="asks",
        tier="all",
        description="Your own approval requests and their state (pending, approved, spent).",
        reason="MCP face of `approvals list`, folded into ddflow_session (D-RA-6)",
        call=_asks,
        payload=("agent", "asks"),
        params=(Param("all", type="boolean", help="Any state.", default=False),),
    ),
    Command(
        path=(),
        tool="ddflow_session_withdraw",
        family="ddflow_session",
        action="withdraw",
        tier="all",
        description="Take back your own approval request while it waits (the asker only).",
        reason="MCP face of `approvals withdraw` / `approvals request` / `approvals list`, folded into ddflow_session (D-RA-6)",
        call=_withdraw,
        payload=("id",),
        params=(Param("id", help="The request id.", required=True),),
    ),
)

_DIGEST = Param(
    "digest",
    default="",
    cli_help="the first characters of the digest `ddflow approvals show` printed (a read-receipt that you looked at this request, not a secret)",
)

COMMANDS: tuple[Command, ...] = (
    *_MCP,
    Command(
        path=("approvals", "list"),
        summary="the approval requests waiting for a person, and the approved ones not yet spent",
        tool="",
        reason="CLI-only with `approvals approve`: the operator's surface",
        params=(Param("all", type="boolean", default=False, cli_help="every request, any state"),),
    ),
    Command(
        path=("approvals", "pending"),
        summary="the requests still waiting for a person, each with its digest and its answer form",
        tool="",
        reason="CLI-only with `approvals approve`: the operator's surface",
        params=(),
    ),
    Command(
        path=("approvals", "log"),
        summary="the audit timeline of approvals (who asked, who answered, which channel and level, what was spent) and what looks wrong",
        tool="",
        reason="CLI-only with the rest of `approvals`: the operator's surface",
        params=(
            Param("verb", default="", cli_help="only this gated verb"),
            Param("agent_filter", default="", cli_help="only this agent's requests"),
            Param("since", default="", cli_help="only requests filed at or after this ISO date"),
        ),
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
        path=("approvals", "ask"),
        summary="the exact AskUserQuestion to put to the person for a request (the harness channel)",
        tool="",
        reason="CLI-only with the rest of `approvals`",
        params=(Param("id", required=True, positional=True, cli_help="the request id"),),
    ),
    Command(
        path=("approvals", "attest"),
        summary="record the person's answer from the harness transcript (never from your word)",
        tool="",
        reason="CLI-only with the rest of `approvals`",
        params=(Param("id", required=True, positional=True, cli_help="the request id"),),
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
