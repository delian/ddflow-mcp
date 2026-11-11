"""The route text of an operator-only refusal (RA-prompt), in one place.

Every refusal that tells an agent an act is the operator's names the same route: file the
ask, show the operator the question, re-run with ``--approval ID``. The wording is the
``approvals`` prompt template (``ddflow prompts show approvals``), so a project can edit it;
the ids and command names that go into it are validated here and are never free text.

This module only words a refusal. It grants nothing and checks nothing: whether an act may
proceed stays in ``consume.py`` and the ask engine.
"""

from __future__ import annotations

import re
from pathlib import Path

from .. import agent_brief as AB
from .. import prompts as P

NAME = "approvals"
#: The CLI and MCP faces of filing an ask (the declared tool table pins them, see
#: tests/test_approvals_hints.py), so the refusal never names a tool that does not exist.
CLI_ASK = "ddflow approvals ask"
MCP_ASK = "ddflow_session action=ask"
#: What a refusal prints before an ask exists: ``--approval request`` files one.
PLACEHOLDER = "ID"
_ID = re.compile(r"Q[0-9a-f]{10}")
#: Text a rendered route must keep, whatever a project's override says: the way back to an
#: answer. An override that drops it is ignored, never half-applied.
_MUST_NAME = ("--approval", "ddflow approvals ask")


#: Used when no template renders: a refusal is never left without its route.
_FALLBACK = (
    "This is the operator's act, not yours. Ask them in chat with `ddflow approvals ask {id}` "
    "(MCP: `ddflow_session action=ask`); `--approval request` files the ask if none exists. "
    "Re-run this command with `--approval {id}` once they have answered."
)


def clean_id(ask_id: str | None) -> str:
    """``ask_id`` when it is a well-formed ask id, else the placeholder: an id comes from
    the log and a refusal is read by an agent, so nothing else is ever printed in its place."""
    return ask_id if ask_id and _ID.fullmatch(ask_id) else PLACEHOLDER


def _section(name: str, repo: Path | None, overrides: dict[str, str] | None) -> str:
    text = P.resolve(NAME, repo, overrides).text
    return dict(AB.split(text)).get(name, "").strip()


#: Wording that tells a reader to answer the request themselves: never in a route, whoever
#: wrote the template. (The words grant nothing; this keeps them from pointing the wrong way.)
_FORBIDDEN = ("approvals approve", "ddflow approve", "self-approve", "yourself", "approve it")
_FLAG = re.compile(r"--approval[ =]+[`'\"]*([^\s`'\".,;:)]+)")
_MAX_CHARS = 1200
#: A route is prose: shell syntax in it is how a second command rides along with the first.
_SHELLISH = re.compile(r"[;|&$<>\\\n\r]|\becho\b|\beval\b")
#: Anything shaped like an ask id: only the bound one may appear in a route.
_ANY_ID = re.compile(r"\bQ[0-9a-f]{10}\b")


def _sound(out: str, ident: str, need: str) -> bool:
    """Whether a rendered route keeps the way back to the answer and names only ``ident``.

    Every ``--approval X`` in it must be the bound id, so a template cannot send the agent
    to spend another request's answer, and it must not tell the agent to answer for the
    operator. A text that fails is never half-used."""
    low = out.lower()
    return (
        0 < len(out) <= _MAX_CHARS
        and all(m in out for m in _MUST_NAME)
        and all(f not in low for f in _FORBIDDEN)
        and not _SHELLISH.search(out)
        and (ident == PLACEHOLDER or re.search(rf"`ddflow approvals ask {ident}`", out))
        and bool(flags := _FLAG.findall(out))
        and all(m in (ident, need) for m in flags)
        and need in flags
        and all(m == ident for m in _ANY_ID.findall(out))
        and out.count("ddflow approvals ask") == 1
        and out.count("--approval") == len(flags)
    )


def _render(
    name: str,
    repo: Path | None,
    overrides: dict[str, str] | None,
    *,
    ident: str,
    need: str = "",
    **vars: str,
) -> str:
    for ov in (overrides, None):  # the project's text first, the shipped text if it is unusable
        try:
            out = P.render(_section(name, repo, ov), **vars).strip()
        except (P.TemplateError, OSError):
            continue
        if _sound(out, ident, need or ident):
            return out
    return ""


def route(
    ask_id: str | None = None, *, repo: Path | None = None, overrides: dict[str, str] | None = None
) -> str:
    """The sentence a refusal ends with: how the operator's answer reaches this command."""
    ask = clean_id(ask_id)
    # No ask yet: the way to file one is `--approval request`, so the text says that.
    section = "route" if ask != PLACEHOLDER else "route_new"
    return _render(
        section,
        repo,
        overrides,
        ident=ask,
        need="request" if section == "route_new" else ask,
        ask_id=ask,
    ) or _FALLBACK.format(id=ask)


def driver_line(repo: Path | None = None, overrides: dict[str, str] | None = None) -> str:
    """The one line a driver or brief carries about operator-only acts."""
    return _render("driver", repo, overrides, ident=PLACEHOLDER)
