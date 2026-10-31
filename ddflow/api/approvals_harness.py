"""`approvals ask` and `approvals attest` (RA-channel-harness): the harness channel's two agent-run
steps. Neither answers by the agent's word: `ask` prints what to put to the person, `attest`
records an answer only when the harness-written transcript holds it (services/approvals/harness)."""

from __future__ import annotations

import json
from pathlib import Path

from ..core import outcome as O
from ..core.handlers.asks import asks_of
from ..services.approvals import harness as H
from ._base import _load


def approvals_ask(repo: Path, ask_id: str, *, agent: str = "") -> O.Outcome:
    """The exact AskUserQuestion input for a waiting request."""
    _log, cfg, st = _load(repo, agent)
    row = asks_of(st).get(ask_id)
    if row is None or not row["verb"]:
        return O.failed("approvals.ask", f"no approval request {ask_id}")
    if not cfg.approvals.accept_harness:
        return O.refused("approvals.ask", "the harness channel is off ([approvals].accept_harness)")
    if row["min_level"] > H.LEVEL:
        if not H.chat_t3(cfg):
            return O.refused("approvals.ask", H.t3_refusal(row))
        digits = cfg.approvals.min_digest_chars
        return O.ok(  # a T3 act: only the typed answer counts, so no AskUserQuestion is offered
            "approvals.ask",
            id=ask_id,
            text=(
                f"'{row['verb']}' is a tier-3 act; the operator opted in to typed chat answers "
                f"(rated L2-typed, no stronger than L2; every use is warned on). Show the person the "
                f"request (`ddflow approvals show {ask_id}`); they type `approve {ask_id} <first "
                f"{digits}+ digest characters>` in their own chat, then run `ddflow approvals attest "
                f"{ask_id}`. An AskUserQuestion answer is not accepted for this act."
            ),
        )
    body = H.payload(row)
    text = (
        f"Put this to the person with AskUserQuestion, unaltered (the question text carries the "
        f"request id and digest), then run `ddflow approvals attest {ask_id}`. The person may instead "
        f"type `approve {ask_id} <first {cfg.approvals.min_digest_chars}+ digest characters>` (or `deny ...`) "
        f"in their own chat:\n" + json.dumps(body, indent=2)
    )
    return O.ok("approvals.ask", id=ask_id, input=body, text=text)


def approvals_attest(repo: Path, ask_id: str, *, agent: str = "") -> O.Outcome:
    """Record the person's answer from the harness transcript, if it is there."""
    log, cfg, _st = _load(repo, agent)
    ok, message = H.attest(log, cfg, ask_id)
    if ok:
        return O.ok("approvals.attest", id=ask_id, text=message)
    if message.startswith(H.WAITING):  # not answered yet: come back, not a refusal
        return O.nothing("approvals.attest", message, id=ask_id)
    return O.refused("approvals.attest", message)
