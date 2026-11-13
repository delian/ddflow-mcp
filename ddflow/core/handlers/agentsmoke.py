"""The fold of `agent.smoke`: the newest live smoke verdict of each agent. Pure."""

from __future__ import annotations

from typing import Any

from ..events import Event
from ..records import State


def smoke_of(st: State) -> dict[str, dict[str, Any]]:
    """agent -> its newest smoke record ({"verdict", "reason", "attempts"})."""
    return st.__dict__.setdefault("smoke", getattr(st, "smoke", {}))


def h_smoke(st: State, ev: Event) -> None:
    agent = str(ev.data.get("agent") or ev.subject)
    smoke_of(st)[agent] = {
        "verdict": str(ev.data.get("verdict") or ""),
        "reason": str(ev.data.get("reason") or ""),
        "attempts": list(ev.data.get("attempts") or []),
    }


AGENTSMOKE_HANDLERS = {"agent.smoke": h_smoke}
