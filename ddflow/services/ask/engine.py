"""One approvals-and-questions engine, local first (SE-ask-core, D-shared-engines-r2 1).

An ASK is a question put to a person, or an approval of something at a content digest. It is
requested, then answered, withdrawn or expired; every step is an event (`core/handlers/asks`),
so the inbox, the audit trail and a replay all read one record. This module holds no transport:
the CLI today, `ddflow serve` and the chat gateways later hand it an `Actor`
(`services/actors.resolve_actor`) and a string, and show the person whatever `request` returns.

The invariants, each pinned by `tests/test_ask_engine.py`:

* HUMAN-ONLY. `answer` records an outcome only for an actor whose role came from a terminal
  (a person at their own shell) or a credential (a paired token the caller verified; also an
  id it proved that `[agent].actors` names), and who
  holds capability ``approve`` -- or ``approve_marked`` for an ask the operator marked
  bot-answerable. An agent, and the whole MCP surface, resolves to the default role: it can
  ask (`request`) and read (`pending`, `verdict`), never answer. Marking an ask bot-answerable
  is itself an ``approve`` act, so an agent cannot route its own ask to a bot.
* DIGEST. An approval is of ``digest``: the answer must present the digest the person saw, and
  `verdict` approves only exactly that digest -- a later edit of the thing is not approved.
* ONE-TIME CODE. ``with_code`` mints a code, shown once and kept only as its hash; an answer
  from anything but a terminal (a remote channel proving the person got the message) must
  present it; a person at the terminal need not. The code is returned to the transport and never stored.
* EXPIRY. An ask lapses at ``expires_at`` (``[agent].ask_ttl_s`` unless given); an answer after
  that is refused. The fold never reads a clock: `status` does.
"""

from __future__ import annotations

import hmac
import secrets
from dataclasses import dataclass
from typing import Any

from ...config import Config
from ...core import clock
from ...core.digest import content_digest
from ...core.handlers.asks import ANSWERED, EXPIRED, PENDING, asks_of
from ...core.model import fold
from ..actors import Actor, require
from ..approval import Verdict, token_hash

APPROVAL = "approval"
QUESTION = "question"
APPROVED = "approved"
DENIED = "denied"
ANSWERED_WORD = "answered"  #: the outcome of a free-text answer; the text itself is `text`
#: Where an answer may come from: a person at a shell, a credential a caller proved, or an id a credential proved that
#: `[agent].actors` names (a gateway).
ANSWER_SOURCES = ("terminal", "credential", "named")
CAP_APPROVE = "approve"
CAP_APPROVE_MARKED = "approve_marked"


class AskRefused(ValueError):
    """The engine will not do that; the message says why and what to do instead."""


@dataclass(frozen=True)
class Requested:
    id: str
    #: The one-time code, "" unless asked for. Shown to the person by the transport; the log
    #: keeps only its hash.
    code: str = ""


def _now(now: float | None) -> float:
    return clock.now_utc().timestamp() if now is None else now


def digest_of(content: str | bytes) -> str:
    """The content digest an approval is of (sha256, hex)."""
    return content_digest(content)


def _state(log):
    return fold(log.read_all(), strict=False)


def request(
    log,
    cfg: Config,
    *,
    subject: str,
    kind: str = APPROVAL,
    text: str = "",
    digest: str = "",
    content: str | bytes = "",
    options: tuple[str, ...] | list[str] = (),
    ttl_s: float | None = None,
    bot_answerable: bool = False,
    with_code: bool = False,
    actor: Actor | None = None,
    now: float | None = None,
) -> Requested:
    """Put an ask. Anyone may ask; ``bot_answerable`` needs ``actor`` to hold ``approve``."""
    if kind not in (APPROVAL, QUESTION):
        raise AskRefused(f"an ask is an '{APPROVAL}' or a '{QUESTION}', not '{kind}'")
    if not subject:
        raise AskRefused("an ask needs a subject: what is being asked about")
    if kind == APPROVAL:
        digest = digest or (digest_of(content) if content else "")
        if not digest:
            raise AskRefused("an approval is of something: give its digest or its content")
    elif not text:
        raise AskRefused("a question needs its text")
    if not isinstance(bot_answerable, bool):
        raise AskRefused("bot_answerable is true or false, not " + repr(bot_answerable))
    if bot_answerable:
        why = require(actor, CAP_APPROVE) if actor is not None else "no actor named"
        if why:
            raise AskRefused(f"only an approver may mark an ask bot-answerable: {why}")
    ttl = cfg.agent.ask_ttl_s if ttl_s is None else ttl_s
    if ttl <= 0:
        raise AskRefused("an ask must expire: ttl_s is above 0 (`agent.ask_ttl_s`)")
    ask_id = "Q" + secrets.token_hex(5)
    code = _mint_code(cfg.agent.ask_code_length) if with_code else ""
    data: dict[str, Any] = {
        "id": ask_id,
        "type": kind,
        "subject": subject,
        "text": text,
        "digest": digest,
        "options": list(options),
        "expires_at": clock.iso_at(_now(now) + ttl),
        "bot_answerable": bool(bot_answerable),
    }
    if code:
        data["code_hash"] = token_hash(code)
    log.append("ask.requested", ask_id, data)
    return Requested(ask_id, code)


def _mint_code(length: int) -> str:
    alphabet = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"  # no 0/O/1/I: read aloud or typed on a phone
    return "".join(secrets.choice(alphabet) for _ in range(max(4, length)))


def status(row: dict[str, Any], now: float | None = None) -> str:
    """pending | answered | withdrawn | expired. A pending ask past ``expires_at`` is expired."""
    if row["state"] != PENDING:
        return row["state"]
    return EXPIRED if clock.epoch(row["expires_at"], default=0.0) <= _now(now) else PENDING


def get(state, ask_id: str) -> dict[str, Any] | None:
    return asks_of(state).get(ask_id)


def pending(state, now: float | None = None) -> list[dict[str, Any]]:
    """The asks still waiting for a person, oldest first (the inbox)."""
    rows = [r for r in asks_of(state).values() if status(r, now) == PENDING]
    return sorted(rows, key=lambda r: r["requested_at"])


def answer(
    log,
    cfg: Config,
    ask_id: str,
    outcome: str,
    *,
    actor: Actor,
    digest: str = "",
    code: str = "",
    text: str = "",
    via: str = "cli",
    now: float | None = None,
) -> dict[str, Any]:
    """Record a person's answer. Raises `AskRefused` unless every invariant holds. Returns the
    folded row. ``outcome`` is approved/denied for an approval, one of the options for a
    question that has them, else 'answered' with the free text in ``text``; ``digest`` is what
    the person saw."""
    if actor.source not in ANSWER_SOURCES:
        raise AskRefused(
            f"refusing: answering is a person's act, and {actor.id} acts as an agent "
            f"({actor.source}). An agent can ask and read; run `ddflow` from your own terminal."
        )
    with log.transaction():  # checked under the log's lock: two answers must not both land
        row = get(_state(log), ask_id)
        if row is None:
            raise AskRefused(f"no ask {ask_id}")
        _check_answerable(row, actor, outcome, digest, code, text, now)
        human = actor.source == "terminal" or actor.can(CAP_APPROVE)
        log.append(
            "ask.answered",
            ask_id,
            {
                "id": ask_id,
                "outcome": outcome,
                "answer": text,
                "digest": digest,
                "by": actor.id,
                "role": actor.role,
                "via": via,
                "human": human,
                "coded": bool(code),
            },
        )
        return dict(get(_state(log), ask_id) or {})


def _check_answerable(row, actor, outcome, digest, code, text, now) -> None:
    ask_id = row["id"]
    state = status(row, now)
    if state == EXPIRED:
        raise AskRefused(f"{ask_id} expired at {row['expires_at']}; ask again")
    if state != PENDING:
        raise AskRefused(f"{ask_id} is already {state}")
    if not actor.can(CAP_APPROVE) and not (row["bot_answerable"] and actor.can(CAP_APPROVE_MARKED)):
        raise AskRefused(
            require(actor, CAP_APPROVE_MARKED if row["bot_answerable"] else CAP_APPROVE)
        )
    if row["type"] == APPROVAL:
        if outcome not in (APPROVED, DENIED):
            raise AskRefused(f"an approval is answered '{APPROVED}' or '{DENIED}'")
        if digest != row["digest"]:
            raise AskRefused(
                f"{ask_id} is of digest {row['digest']}, not {digest or 'none given'}: "
                "look at what is being approved and present its digest"
            )
    elif row["options"]:
        if outcome not in row["options"]:
            raise AskRefused(f"{ask_id} is answered with one of: {', '.join(row['options'])}")
    elif outcome != ANSWERED_WORD or not text:
        # free text goes in `text` (redacted in the log), never in the compared `outcome`
        raise AskRefused(f"{ask_id} takes free text: outcome '{ANSWERED_WORD}' and the text")
    if row["code_hash"] and actor.source != "terminal":
        if not code or not hmac.compare_digest(token_hash(code), row["code_hash"]):
            raise AskRefused(f"{ask_id} needs the one-time code it was sent with")


def withdraw(log, ask_id: str, *, actor: Actor, now: float | None = None) -> None:
    """The asker (or an approver) takes a still-open ask back. Recorded as the actor."""
    with log.transaction():
        row = get(_state(log), ask_id)
        if row is None or status(row, now) != PENDING:
            raise AskRefused(f"{ask_id} is not pending")
        if actor.id != row["requested_by"] and not actor.can(CAP_APPROVE):
            raise AskRefused(f"only {row['requested_by']} or an approver may withdraw {ask_id}")
        log.append("ask.withdrawn", ask_id, {"id": ask_id, "by": actor.id})


def sweep(log, now: float | None = None) -> list[str]:
    """Write the audit mark for every pending ask whose time has run out; return their ids."""
    done: list[str] = []
    with log.transaction():
        for row in list(asks_of(_state(log)).values()):
            if status(row, now) == EXPIRED and row["state"] == PENDING:
                log.append("ask.expired", row["id"], {"id": row["id"], "by": "sweep"})
                done.append(row["id"])
    return done


def verdict(state, subject: str, digest: str, now: float | None = None) -> Verdict:
    """Is ``subject`` approved as it is NOW (``digest``)? The consumer's one question: the
    latest answered approval of that subject must be 'approved', of exactly this digest, given
    by a person (or by a bot on an ask marked bot-answerable)."""
    rows = [r for r in asks_of(state).values() if r["subject"] == subject and r["type"] == APPROVAL]
    rows.sort(key=lambda r: r["requested_at"])
    answered = [r for r in rows if r["state"] == ANSWERED]
    if not answered:
        waiting = any(status(r, now) == PENDING for r in rows)
        return Verdict(False, f"{subject} is " + ("waiting for an answer" if waiting else "not approved"))
    last = answered[-1]
    if last["outcome"] != APPROVED:
        return Verdict(False, f"{subject} was denied by {last['answered_by']}", last)
    if not (last["human"] or last["bot_answerable"]):
        return Verdict(False, f"{subject}: the answer was not a person's", last)
    if last["digest"] != digest or last["answered_digest"] != digest:
        return Verdict(
            False,
            f"{subject} changed since it was approved (approved {last['answered_digest']}, "
            f"now {digest})",
            last,
        )
    return Verdict(True, f"{subject} approved by {last['answered_by']}", last)
