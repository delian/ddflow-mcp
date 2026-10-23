"""What the open `approvals` commands (list, show, request, withdraw) need from the ask engine,
and nothing that answers. `api/` reaches the engine only through here, so that no agent-facing
module can record an answer (tests/test_ask_engine.py::test_no_mcp_or_api_module_can_record_an_answer);
the person-only verbs (approve, deny, revoke) live in `surfaces/commands/approvals.py`."""

from __future__ import annotations

import time
from typing import Any

from ...core.handlers.asks import asks_of
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


def waiting_for_a_person(state, now: float | None = None) -> list[dict[str, Any]]:
    """The command approvals still waiting for a person to answer (oldest first)."""
    now = time.time() if now is None else now
    rows = [r for r in asks_of(state).values() if r["verb"] and E.status(r, now) == "pending"]
    return sorted(rows, key=lambda r: r["requested_at"])


def pending_line(state, shown: int = 3) -> str:
    """One line for the brief and the status when a request waits for the operator, or ""."""
    rows = waiting_for_a_person(state)
    if not rows:
        return ""
    names = ", ".join(f"{r['id']} ({r['verb']} for {r['requested_by']})" for r in rows[:shown])
    more = f" (+{len(rows) - shown} more)" if len(rows) > shown else ""
    return (
        f"{len(rows)} approval request(s) wait for the operator: {names}{more}. "
        "`ddflow approvals list`; a person answers with `ddflow approvals approve ID`."
    )
