"""The `pair` and `tokens` commands, declared once (P-remote.auth). CLI-ONLY: minting and ending
a credential is the operator's act, and a token or code in a tool result would sit in an agent's
context and its transcript. `ddflow serve` reaches them through the same table with an operator
token, so a client without a shell has a route once it lands."""

from __future__ import annotations

import getpass
import importlib
import sys

from ...core import outcome as O
from ..registry import CliPolicy, Command, Param

_REASON = "a person's act (P-remote.auth): a minted secret must not enter an agent's context"
_TTL = Param(
    "ttl_h", type="integer", flag="--ttl-h",
    cli_help="hours the token lives ([agent].token_ttl_h unless given, up to token_max_ttl_h)",
)  # fmt: skip


def _api():
    return importlib.import_module("ddflow.api.auth")


def _text(out, _a) -> str:
    return out.data["text"]


def _read_code() -> str:
    """The code from stdin, never argv: `ps` and a shell history keep an argument."""
    if sys.stdin is not None and sys.stdin.isatty():
        return getpass.getpass("pairing code: ")
    return sys.stdin.readline().strip() if sys.stdin is not None else ""


def _redeem(repo, args, agent, code=None) -> O.Outcome:
    """``code``: what the CLI read from stdin. A transport hands it in ``args`` instead, since
    a server's own stdin is not its client's."""
    return _api().redeem(repo, args.get("code", "") if code is None else code, agent)


_CLI = CliPolicy(typed_agent=True, shown=(O.OK,))
_CLI_REDEEM = CliPolicy(
    typed_agent=True, shown=(O.OK,), call_kwargs=lambda _ctx: {"code": _read_code()}
)

COMMANDS: tuple[Command, ...] = (
    Command(
        path=("pair",),
        summary="a PERSON mints a one-time code for a named client with a role (`tokens redeem` spends it)",
        tool="",
        reason=_REASON,
        call=lambda repo, a, agent: _api().pair(repo, a, agent),
        payload=("code", "name", "role", "expires_at"),
        render=_text,
        cli=_CLI,
        params=(
            Param(
                "name",
                required=True,
                positional=True,
                cli_help="the client: its token's name is its identity",
            ),
            Param("role", required=True, cli_help="its role, a name in [agent].roles"),
            _TTL,
        ),
    ),
    Command(
        path=("tokens", "redeem"),
        summary="exchange a one-time code (read from stdin) for the token, shown once",
        tool="",
        reason=_REASON,
        call=_redeem,
        payload=("token", "id", "name", "role", "expires_at"),
        render=_text,
        cli=_CLI_REDEEM,
    ),
    Command(
        path=("tokens", "list"),
        summary="who holds a token: names, roles and dates from the log, never a secret",
        tool="",
        reason="CLI-only with the rest of `tokens`: the operator's surface",
        call=lambda repo, a, agent: _api().tokens_list(repo, a, agent),
        payload=("tokens",),
        render=_text,
        cli=_CLI,
        params=(Param("all", type="boolean", default=False, cli_help="also expired and revoked"),),
    ),
    Command(
        path=("tokens", "revoke"),
        summary="a PERSON ends a token (by name or id) and any pairing waiting for that name",
        tool="",
        reason=_REASON,
        call=lambda repo, a, agent: _api().revoke(repo, a, agent),
        payload=("revoked",),
        render=_text,
        cli=_CLI,
        params=(
            Param("name", required=True, positional=True, cli_help="the token's name or id"),
            Param("reason", required=True, cli_help="why this credential ended"),
        ),
    ),
    Command(
        path=("tokens", "rotate"),
        summary="a PERSON mints a code that replaces a token; the old one works until it is redeemed",
        tool="",
        reason=_REASON,
        call=lambda repo, a, agent: _api().rotate(repo, a, agent),
        payload=("code", "name", "role", "expires_at"),
        render=_text,
        cli=_CLI,
        params=(
            Param("name", required=True, positional=True, cli_help="the token's name or id"),
            _TTL,
        ),
    ),
)
