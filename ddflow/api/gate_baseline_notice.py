"""An automatic baseline-red acceptance is told to the coordinator, once (D-baseline-red-auto-accept).

Three records of the same fact: an entry of the approvals log answered by the standing
decision (`policy:baseline_red_auto`, so `approvals log` and `doctor` show it, the same verb a
person's `ddflow approve` uses), a message in the coordinator's inbox, and the count that
`doctor` compares with `[gates].baseline_red_rate_warn`. Told once per recorded outcome and
failing ids: re-reading the gate or recompleting the item says nothing more."""

from __future__ import annotations

import json
from pathlib import Path

from ..core.digest import content_digest
from ..core.handlers.asks import asks_of
from ..core.model import fold
from ..services import gate_accept as GA
from ..services import lanerelease as LR
from ..services import logblobs as LB
from ..services.approvals import policy as PO
from ..services.approvals.canon import Canon
from ._base import _load

RULE = "baseline_red_auto"
SHOWN = 8


def marker(item: str, gate: str, ids: list[str]) -> str:
    """Leads the recorded text (a cut cannot remove it): which failing set this told."""
    return f"[{RULE} {item}.{gate} {content_digest(json.dumps(sorted(ids)))[:10]}]"


def notice_text(item: str, gate: str, ids: list[str], bug: str) -> str:
    shown = ", ".join(ids[:SHOWN]) + (f" (+{len(ids) - SHOWN} more)" if len(ids) > SHOWN else "")
    return (
        f"{item}.{gate} is PARTIAL and was accepted automatically as baseline-red: {shown} fail "
        f"on the base branch too and are not in the item's reach. Bug: {bug or 'not filed'}. "
        f"A test failing only in the item (a timing flake such as test_lookup_latency_budget) is a NEW failure and keeps the gate failed. Fix the base; to withdraw the acceptance set `ddflow config gates.baseline_red_auto off`."
    )


def notify(repo: Path, agent: str, item: str, gate: str) -> bool:
    """Record and tell the automatic acceptance of ``item``'s ``gate`` (False = nothing to say
    or already said)."""
    log, cfg, _st = _load(repo, agent)
    it = fold(log.read_all(), strict=False).items.get(item)
    base = (
        (LB.full(it.gates[gate].evidence or {}).get("baseline") or {})
        if it and gate in it.gates
        else {}
    )
    auto = base.get("auto") or {}
    if (
        it is None
        or not auto.get("accepted")
        or GA.refusal(it, gate)
        or cfg.gates.baseline_red_auto != "on"
    ):
        return False
    ids = list(auto.get("ids") or [])
    canon = Canon(verb="approve.partial", agent=log.agent_id or agent or "ddflow", item=item,
                  target=gate, state=GA.digest(it, gate))  # fmt: skip
    state = fold(log.read_all(), strict=False)
    mark = marker(item, gate, ids)
    if _said(state, canon, mark):
        return False
    text = f"{mark} " + notice_text(item, gate, ids, str(base.get("bug") or ""))
    PO.record_auto(log, cfg, canon, text, RULE)  # the durable record: the inbox below is advisory
    if cfg.gates.baseline_red_notify_to:
        LR.notify(
            repo,
            cfg,
            cfg.gates.baseline_red_notify_to,
            text,
            item=item,
            reason="message",
            sender=canon.agent,
        )
    return True


def _said(state, canon: Canon, text: str) -> bool:
    """Was this item's gate already told for these same failing ids (a re-run records a new
    outcome, so the digest cannot be the key)?"""
    return any(
        r["policy"] and r["answered_by"] == f"policy:{RULE}" and r["subject"] == canon.subject()
        and text in r["text"]
        for r in asks_of(state).values()
    )  # fmt: skip
