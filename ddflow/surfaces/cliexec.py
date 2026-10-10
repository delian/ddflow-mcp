"""The CLI executor: run a declared `Command` from a parsed command line.

A command declares `call` (MCP-shaped arguments in, an `Outcome` out), `payload` (the
fields of the wire body) and, for the human, `render`. This module is the one place that
turns a parsed namespace into those arguments, calls, and prints, so a verb that is
declared once needs no hand-written `cmd_*` function: its `--json` body is the MCP
tool's body, its refusal has the MCP tool's lead, and its human line is `render`.

What does NOT fit `call` + `render` (read by the family slices; R-uc-cli-executor-pilot):
stdin, streaming output, progress, and an exit code that is not the Outcome's. Those
commands keep an explicit `handler`. What differs only in how the result is printed (the
identity as typed, stderr notes, which exits show a result, a CLI body that is not the
tool's) is a `registry.CliPolicy` on the command, and ``called_from`` is passed to a
command that declares ``wants_called_from``.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from typing import Any

from ..core.outcome import EXIT_NAMES, FAIL, REFUSED
from ..core.plain import plain
from .registry import Command
from .render import emit_json


def arguments(cmd: Command, ns: argparse.Namespace) -> dict[str, Any]:
    """The MCP-shaped argument dict of a parse: each CLI parameter the command declares,
    under its own name, left out when the line did not supply it (argparse's ``None``)."""
    given = ((p.name, getattr(ns, p.name, None)) for p in cmd.cli_params)
    return {name: value for name, value in given if value is not None}


def _call(cmd: Command, args: dict[str, Any], ctx: Any) -> Any:
    """``cmd.call`` for the parsed ``args``, as the identity and from the place its policy
    says (`registry.CliPolicy`)."""
    policy = cmd.cli
    if policy is not None and policy.typed_agent:
        agent = ctx.requested_agent
    else:
        agent = ctx.log.agent_id if getattr(ctx, "log", None) else ""
    more: dict[str, Any] = {}
    if cmd.wants_called_from:
        more["called_from"] = ctx.called_from
    if policy is not None and policy.extra is not None:
        more.update(policy.extra(ctx))
    return cmd.call(ctx.repo, args, agent, **more)


def _run_policy(cmd: Command, args: dict[str, Any], out: Any, ctx: Any) -> int:
    """Print ``out`` the way ``cmd.cli`` says: the notes, then the result when the exit is
    one the command shows, else the reason alone on stderr."""
    policy = cmd.cli
    for line in policy.notes(out, args, ctx) if policy.notes else ():
        print(line, file=sys.stderr)
    if not _shown(policy.shown, out):
        print(policy.reason(out) if policy.reason else out.reason, file=sys.stderr)
    elif ctx.json:
        body = policy.body(out, args) if policy.body else out.body(cmd.payload)
        emit_json(plain(body))
    else:
        text = (
            policy.human(out, args, ctx)
            if policy.human
            else (cmd.render(out, args) if cmd.render else "") or out.reason
        )
        if text is not None:
            print(text)
    return int(out.exit)


def _shown(shown: Any, out: Any) -> bool:
    """Whether ``out`` is printed as a result (`CliPolicy.shown`)."""
    if shown is None:
        return True
    return shown(out) if callable(shown) else out.exit in shown


def run(cmd: Command, ns: argparse.Namespace, ctx: Any) -> int:
    """Call ``cmd`` for the parsed line ``ns`` and print its result; the exit code is the
    Outcome's. An error goes to stderr alone; a refusal under ``--json`` is the body with
    the MCP tool's lead (the reason, then the body), and a refusal that is a duplicate
    check's answer says why on stderr too; otherwise the human reads ``render``. A command
    with a ``cli`` policy prints as that says instead (`_run_policy`)."""
    args = arguments(cmd, ns)
    out = _call(cmd, args, ctx)
    if cmd.cli is not None:
        return _run_policy(cmd, args, out, ctx)
    if out.exit == FAIL:
        print(out.reason, file=sys.stderr)
        return int(FAIL)
    if ctx.json:
        if out.exit and out.data.get("candidates"):
            print(out.reason, file=sys.stderr)
        body = out.body(cmd.payload)
        if out.exit == REFUSED and isinstance(body, dict):
            lead = {"reason": out.reason, "outcome": EXIT_NAMES[REFUSED], "exit": REFUSED}
            body = {"refusal": lead, **body}
        emit_json(body)
    elif out.exit == REFUSED or (out.exit and out.data.get("candidates")):
        print(out.reason, file=sys.stderr)
    else:
        print((cmd.render(out, args) if cmd.render else "") or out.reason)
    return int(out.exit)


def handler(cmd: Command) -> Callable[[argparse.Namespace, Any], int]:
    """``cmd``'s CLI function (``fn(ns, ctx) -> exit``): `run`, bound to the command."""

    def fn(ns: argparse.Namespace, ctx: Any) -> int:
        return run(cmd, ns, ctx)

    return fn
