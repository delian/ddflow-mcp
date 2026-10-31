"""The config-write guard of the typed-chat opt-in (D-RA-T3-chat): no tool, so no agent and no
chat answer, turns `[approvals].accept_chat_for_t3` on or lifts `forbid_chat_for_t3`.

It is the person-only half of the opt-in: the operator edits `.ddflow/local/config.toml` by hand on
the host (or answers it in `ddflow setup`, a follow-up). It judges the RESULT of an edit against the
file before it, like the other guards, and reads no identity: a tool write is refused for everyone."""

from __future__ import annotations

from typing import Any

ON = "accept_chat_for_t3"
FORBID = "forbid_chat_for_t3"


def _held(data: dict[str, Any], knob: str) -> bool:
    return bool((data.get("approvals") or {}).get(knob))


def problem(prev: dict[str, Any], now: dict[str, Any]) -> str:
    """Why this edit may not be written by a tool, or ""."""
    if _held(now, ON) and not _held(prev, ON):
        return (
            f"[approvals].{ON} lets a typed chat answer approve a tier-3 act: no tool writes it. "
            "The operator sets it by editing .ddflow/local/config.toml on the host (it counts "
            "only there). It cannot be enabled remotely the first time."
        )
    if _held(prev, FORBID) and not _held(now, FORBID):
        return (
            f"[approvals].{FORBID} only tightens: no tool lifts it. The operator edits the file "
            "that sets it."
        )
    return ""
