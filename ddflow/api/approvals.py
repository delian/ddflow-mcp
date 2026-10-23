"""`ddflow approvals ...` (RA-cli): request, list, show, approve, deny, revoke, withdraw.

Agents may request, list, show and withdraw their own; approve, deny and revoke are a PERSON's
acts, refused to every agent identity before anything is read or written (the ask engine
refuses them again). The person is shown the text ddflow rendered and answers with the digest
they saw, never the agent's description (D-RA-2)."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from ..core import outcome as O
from ..core.handlers.asks import asks_of
from ..services import actors as A
from ..services.approvals import request as R
from ..services.approvals import state as PS
from ..services.approvals import view as V
from ..services.approvals.canon import Canon, UnknownVerb
from ._base import _load


def _commands(st, now: float) -> list[dict[str, Any]]:
    rows = [r for r in asks_of(st).values() if r["verb"]]
    return sorted(rows, key=lambda r: r["requested_at"])


def _status(row: dict[str, Any], now: float) -> str:
    if row["state"] == "answered":
        word = row["outcome"] if row["outcome"] != "approved" else "approved"
        spent = f", {row['used']}/{row['uses']} used" if row["outcome"] == "approved" else ""
        return word + spent
    return V.status(row, now)


def _line(row: dict[str, Any], now: float) -> str:
    return f"{row['id']}  {_status(row, now):<14} {row['requested_by']:<14} {row['subject'][:70]}"


def approvals_list(repo: Path, *, all_rows: bool = False, agent: str = "") -> O.Outcome:
    _log, _cfg, st = _load(repo, agent)
    now = time.time()
    rows = _commands(st, now)
    if not all_rows:
        rows = [
            r for r in rows
            if V.status(r, now) == "pending" or (r["state"] == "answered" and r["used"] < r["uses"] and r["outcome"] == "approved")
        ]  # fmt: skip
    text = "\n".join(_line(r, now) for r in rows) or "no pending or unspent approvals"
    return O.ok("approvals.list", approvals=[_row(r, now) for r in rows], text=text)


def _row(r: dict[str, Any], now: float) -> dict[str, Any]:
    return {
        "id": r["id"], "verb": r["verb"], "agent": r["requested_by"], "status": _status(r, now),
        "tier": r["tier"], "min_level": r["min_level"], "digest": r["digest"],
        "expires_at": r["expires_at"], "used": r["used"], "uses": r["uses"],
    }  # fmt: skip


def approvals_show(repo: Path, ask_id: str, *, agent: str = "") -> O.Outcome:
    _log, cfg, st = _load(repo, agent)
    row = asks_of(st).get(ask_id)
    if row is None or not row["verb"]:
        return O.failed("approvals.show", f"no approval request {ask_id}")
    now = time.time()
    tail = f"\n  status  : {_status(row, now)}  (expires {row['expires_at']})"
    if row["state"] == "answered":
        tail += f"\n  answered: {row['outcome']} by {row['answered_by']} via {row['via']}, level L{V.verified_level(row, cfg)}"
    return O.ok("approvals.show", approval=_row(row, now), text=row["text"] + tail)


def approvals_request(
    repo: Path, verb: str, *, item: str = "", target: str = "", reason: str = "", agent: str = ""
) -> O.Outcome:
    """An agent files the request itself (what a refusal does when `auto_request` is on)."""
    log, cfg, st = _load(repo, agent)
    provider = PS.PROVIDERS.get(verb)
    if provider is None:
        return O.failed(
            "approvals.request",
            f"'{verb}' cannot be requested by hand yet: run the command; its refusal files the "
            f"request (verbs with a request provider: {', '.join(sorted(PS.PROVIDERS)) or 'none'})",
        )
    try:
        canon = Canon(verb=verb, agent=log.agent_id, item=item, target=target,
                      state=provider(st, item, target, {}))  # fmt: skip
    except (UnknownVerb, ValueError) as exc:
        return O.failed("approvals.request", str(exc))
    filed = R.file(log, cfg, st, canon, reason=reason)
    word = "requested" if filed.new else "is still waiting"
    return O.ok(
        "approvals.request", id=filed.id, new=filed.new, digest=filed.digest,
        text=f"approval {filed.id} {word}: the operator approves it with `ddflow approvals approve {filed.id}`",
    )  # fmt: skip


def approvals_withdraw(repo: Path, ask_id: str, *, agent: str = "") -> O.Outcome:
    """The asker (or a person) takes back a request still waiting."""
    log, cfg, _st = _load(repo, agent)
    actor = A.resolve_actor(cfg, log.agent_id, requested_agent=agent)
    try:
        V.withdraw(log, ask_id, actor=actor)
    except V.AskRefused as exc:
        return O.refused("approvals.withdraw", str(exc))
    return O.ok("approvals.withdraw", id=ask_id, text=f"{ask_id} withdrawn")
