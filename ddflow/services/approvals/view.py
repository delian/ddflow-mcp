"""What the open `approvals` commands (list, show, request, withdraw) need from the ask engine,
and nothing that answers. `api/` reaches the engine only through here, so that no agent-facing
module can record an answer (tests/test_ask_engine.py::test_no_mcp_or_api_module_can_record_an_answer);
the person-only verbs (approve, deny, revoke) live in `surfaces/commands/approvals.py`."""

from __future__ import annotations

from typing import Any

from ..actors import Actor
from ..ask import engine as E

AskRefused = E.AskRefused


def status(row: dict[str, Any], now: float) -> str:
    return E.status(row, now)


def verified_level(row: dict[str, Any]) -> int:
    return E.verified_level(row)


def withdraw(log, ask_id: str, *, actor: Actor, now: float | None = None) -> None:
    """The asker takes back a request still waiting (an approver may too)."""
    E.withdraw(log, ask_id, actor=actor, now=now)
