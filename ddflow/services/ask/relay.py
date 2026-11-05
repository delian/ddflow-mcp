"""Put an ask through a `HumanChannel` and record what comes back (ADP-notify-ask).

The one bridge between the port and the ask engine. A channel only reports; `relay` records, and
it records through `engine.answer`, so the digest comparison, the one-time code and the refusal of
an agent identity stay in the engine for every adapter: a new channel cannot forget them.

`actor` is who runs this, resolved by the caller from the real identity, and it is what the answer
is recorded under. An agent may relay: it puts the ask to the person and reads the outcome. If an
answer comes back that only `actor` could vouch for, the engine refuses it for an agent. The one
channel that vouches for itself is the harness bridge: `attest` proves the answer from the
transcript the harness wrote. What it reports is not taken on its word: the row must be answered
through that channel, and the caller gets the row's outcome, person and digest, not the report.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from ...adapters.notify import ANSWERED, Answer, Approval, HumanChannel
from ...config import Config
from ...core.model import fold
from ..actors import Actor
from . import engine as E


def _row(log, ask_id: str) -> dict[str, Any] | None:
    return E.get(fold(log.read_all(), strict=False), ask_id)


def _put(channel: HumanChannel, row: dict[str, Any], code: str) -> Answer:
    if row["type"] == E.APPROVAL:
        return channel.relay(
            Approval(row["id"], row["text"] or row["subject"], row["digest"], code)
        )
    return channel.ask(row["text"], tuple(row["options"]))


def _record(log, cfg: Config, row: dict[str, Any], got: Answer, actor: Actor, now) -> None:
    free_text = row["type"] == E.QUESTION and not row["options"]
    E.answer(
        log,
        cfg,
        row["id"],
        E.ANSWERED_WORD if free_text else got.text,
        actor=actor,
        digest=got.digest,
        code=got.code,
        text=got.text if free_text else "",
        via=got.how,
        proof=got.proof,
        now=now,
    )


def _as_logged(log, ask_id: str, got: Answer, now: float | None) -> Answer:
    """``got`` of a channel that says it recorded the answer, as the log has it."""
    row = _row(log, ask_id)
    if row is None or E.status(row, now) != ANSWERED or row["via"] != got.how:
        raise E.AskRefused(f"{got.how} says it recorded {ask_id}, and the log has no such answer")
    return replace(
        got,
        text=row["answer"] or row["outcome"],
        who=row["answered_by"],
        digest=row["answered_digest"],
        proof=row["proof"],
    )


def relay(
    log,
    cfg: Config,
    channel: HumanChannel,
    ask_id: str,
    *,
    actor: Actor,
    code: str = "",
    now: float | None = None,
) -> Answer:
    """Put ``ask_id`` to the person on ``channel``. Returns what the channel reported; an answered
    one is in the log by then. Raises `engine.AskRefused` for an ask that is not pending and for
    any answer the engine will not take. ``code`` is the ask's one-time code, for the channel to
    show (the log keeps only its hash)."""
    row = _row(log, ask_id)
    if row is None:
        raise E.AskRefused(f"no ask {ask_id}")
    if (state := E.status(row, now)) != E.PENDING:
        raise E.AskRefused(f"{ask_id} is already {state}")
    got = _put(channel, row, code)
    if got.status != ANSWERED:
        return got
    if got.recorded:
        return _as_logged(log, ask_id, got, now)
    _record(log, cfg, row, got, actor, now)
    return got
