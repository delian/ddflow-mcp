"""The audit view of approvals (RA-audit, D-RA-5): who asked for what, who answered over which
channel at which level, what was spent, and what looks wrong.

`rows` is the timeline `ddflow approvals log` prints; `findings` is what `doctor` should warn about
(a hand-written answer with no proof, an approval left to expire unspent, a spend below the
verb's level, and the standing limits of the channels in use). All read the fold only."""

from __future__ import annotations

import time
from typing import Any

from ...config import Config
from ...core import clock
from ...core.handlers.asks import ANSWERED, asks_of
from ..ask import engine as E


def _commands(state) -> list[dict[str, Any]]:
    return sorted(
        (r for r in asks_of(state).values() if r["verb"]), key=lambda r: r["requested_at"]
    )


def rows(
    state,
    cfg: Config,
    *,
    verb: str = "",
    agent: str = "",
    since: float = 0.0,
    now: float | None = None,
) -> list[dict[str, Any]]:
    now = time.time() if now is None else now
    out = []
    for r in _commands(state):
        if (verb and r["verb"] != verb) or (agent and r["requested_by"] != agent):
            continue
        if clock.epoch(r["requested_at"], default=0.0) < since:
            continue
        out.append(
            {
                "id": r["id"],
                "verb": r["verb"],
                "agent": r["requested_by"],
                "requested_at": r["requested_at"],
                "status": E.status(r, now),
                "tier": r["tier"],
                "min_level": r["min_level"],
                "digest": r["digest"][:16],
                "outcome": r["outcome"],
                "approver": r["answered_by"] if r["state"] == ANSWERED else "",
                "channel": r["via"] if r["state"] == ANSWERED else "",
                "level": E.verified_level(r, cfg) if r["state"] == ANSWERED else 0,
                "used": r["used"],
                "uses": r["uses"],
                "spent_by": [c["by"] for c in r["consumed"]],
            }
        )
    return out


def line(r: dict[str, Any]) -> str:
    who = f" by {r['approver']} via {r['channel']} L{r['level']}" if r["approver"] else ""
    spent = f", spent {r['used']}/{r['uses']}" if r["outcome"] == E.APPROVED else ""
    return (
        f"{r['requested_at'][:19]}  {r['id']}  {r['verb']:<24} {r['agent']:<14} "
        f"{r['status']}{' ' + r['outcome'] if r['outcome'] else ''}{who}{spent}  {r['digest']}"
    )


def findings(state, cfg: Config, *, now: float | None = None) -> list[str]:
    """What an operator should be told, one line each."""
    now = time.time() if now is None else now
    out: list[str] = []
    for r in _commands(state):
        if r["state"] != ANSWERED or r["outcome"] != E.APPROVED:
            continue
        verdict = E.judge(r, r["digest"])
        if not verdict.ok:
            out.append(
                f"{r['id']} ({r['verb']}): an approved answer without proof -- {verdict.reason} "
                "(written by hand?); it cannot be spent"
            )
        level = E.verified_level(r, cfg)
        if r["used"] and level < r["min_level"]:
            out.append(
                f"{r['id']} ({r['verb']}) was spent at level L{level}, below the L{r['min_level']} "
                "its tier needs"
            )
        left = clock.epoch(r["answered_at"], default=0.0) + r["use_ttl_s"] - now
        if r["used"] < r["uses"] and r["use_ttl_s"] and left < cfg.approvals.expiry_warn_s:
            when = "expired unused" if left <= 0 else "will expire unused within minutes"
            out.append(f"{r['id']} ({r['verb']}) approved by {r['answered_by']} {when}")
    out.extend(standing_notes(cfg, state))
    return out


def standing_notes(cfg: Config, state) -> list[str]:
    """The honest limits of the channels in use (D-RA-Q5, D-RA-Q6), once each."""
    notes = []
    if cfg.approvals.accept_harness:
        notes.append(
            "approvals: chat answers (AskUserQuestion, typed) are read from the session transcript, "
            "which the agent's own user can edit; they show a person answered, they do not prove it"
        )
    notes.append(
        "approvals: no approval signer pinned -- T3 acts (command reviewers, the approval "
        "configuration) are rated no stronger than L1 on a single-user host and stay terminal-only"
    )
    return notes
