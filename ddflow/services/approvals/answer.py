"""The person-only half of the ask engine for gated commands: answering and revoking (RA-cli).

Kept in its own module so the open commands (`view`) never import it and the layering is plain:
`api/approvals.py` reads and requests, `api/approvals_answer.py` answers, and no declared
command with a tool reaches the second (tests/test_approvals_cli.py). The engine refuses a
non-person again; this module only resolves who the person is."""

from __future__ import annotations

from ..actors import Actor, resolve_actor
from ..approval import agent_marker as _agent_marker
from ..approval import os_user
from ..ask import engine as E

ANSWER_SOURCES = E.ANSWER_SOURCES
APPROVED = E.APPROVED
DENIED = E.DENIED
AskRefused = E.AskRefused


def agent_marker(agent: str) -> str:
    """Why this call is an agent's ("" for a person at a terminal); checked before any read."""
    return _agent_marker(agent)


def person(cfg, agent: str) -> Actor:
    """The OS user at this shell, as an actor: a terminal unless the call carries an agent
    marker (`--agent`, DDFLOW_AGENT, a harness)."""
    return resolve_actor(cfg, os_user(), requested_agent=agent)


def answer(log, cfg, ask_id: str, outcome: str, *, actor: Actor, digest: str) -> None:
    E.answer(log, cfg, ask_id, outcome, actor=actor, digest=digest, via="cli", level=1)


def revoke(log, ask_id: str, *, actor: Actor) -> None:
    E.revoke(log, ask_id, actor=actor)
