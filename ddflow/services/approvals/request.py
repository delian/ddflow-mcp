"""File an operator-approval request for a gated command (RA-request, D-RA-1/2/Q12).

The refusal site (or the agent) calls `file`: an ask on the one ask engine whose digest is the
canonical command's, whose text is rendered HERE from that record (never written by the agent),
and which only the requesting agent may later spend. Agents may request and read; answering is
the engine's person-only act. The same pending request is returned instead of a second one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ...config import Config
from ..ask import engine as E
from .canon import Canon

_CONTROL = re.compile("[\x00-\x1f\x7f-\x9f" + chr(0x2028) + chr(0x2029) + "]+")


@dataclass(frozen=True)
class Filed:
    id: str
    #: False when an identical request was already waiting and is returned unchanged.
    new: bool
    digest: str


def untrusted(reason: str, limit: int) -> str:
    """The agent's words as one quoted, control-free, clipped line the person reads as data."""
    if limit <= 0:
        return '"(not shown)"'
    flat = _CONTROL.sub(" ", reason).strip()
    if len(flat) > limit:
        flat = flat[: limit - 1] + "…"
    return '"' + flat.replace("\\", "\\\\").replace('"', '\\"') + '"'


def render(
    canon: Canon, reason: str = "", limit: int = 300, uses: int = 1, ask_id: str = ""
) -> str:
    """The text the person answers, built from the canonical record only."""
    lines = [
        f"Approve gated command '{canon.verb}' for agent {canon.agent}?"
        + (f"  [request {ask_id}]" if ask_id else ""),
        f"  command : {s.command}",
        f"  item    : {canon.item or '-'}    target: {canon.target or '-'}",
        f"  acts on : {s.digest}",
        f"  digest  : {canon.digest()[:16]}  (the state is read again when the agent runs it)",
        f"  needs   : tier {s.tier}, assurance level L{s.min_level} or stronger; {uses} use(s)",
    ]
    if canon.args:
        lines.append("  args    : " + ", ".join(f"{k}={v}" for k, v in canon.args))
    lines.append(
        f"  agent's reason (untrusted, not part of the approval): {untrusted(reason, limit)}"
    )
    return "\n".join(lines)


def command_of(canon: Canon, cfg: Config) -> E.Command:
    """What makes the ask a gated command's: verb, tier, level, one use, the use window, the record."""
    return E.Command(
        verb=canon.verb,
        tier=s.tier,
        min_level=s.min_level,
        uses=1,
        use_ttl_s=cfg.approvals.use_ttl_s,
        canon=canon.json(),
    )


def pending_for(state, canon: Canon, now: float | None = None) -> dict | None:
    """An open request for exactly this command by this agent, if one is waiting."""

    want = canon.digest()
    for row in E.pending(state, now):
        if (
            row["verb"] == canon.verb
            and row["digest"] == want
            and row["subject"] == canon.subject()
            and row["requested_by"] == canon.agent
        ):
            return row
    return None


def file(
    log,
    cfg: Config,
    state,
    canon: Canon,
    *,
    reason: str = "",
    now: float | None = None,
    auto: bool = False,
) -> Filed | None:
    """Request an approval for ``canon`` (or return the identical one still waiting).

    ``auto`` marks a filing the REFUSAL SITE makes on the agent's behalf: it honours
    `[approvals].auto_request` (D-RA-Q12) and returns None when that is off."""
    if auto and not cfg.approvals.auto_request:
        return None
    digest = canon.digest()
    if (row := pending_for(state, canon, now)) is not None:
        return Filed(row["id"], False, digest)
    ask_id = E.new_ask_id()  # in the text: no two requests share a question, whatever their digest
    asked = E.request(
        log,
        cfg,
        ask_id=ask_id,
        subject=canon.subject(),
        text=render(canon, reason, cfg.approvals.reason_chars, uses=1, ask_id=ask_id),
        digest=digest,
        now=now,
        command=command_of(canon, cfg),
    )
    return Filed(asked.id, True, digest)
