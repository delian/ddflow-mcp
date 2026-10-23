"""Standing approval rules (RA-policy, D-RA-4, D-RA-Q8): an operator who switched a rule on has
decided in advance, within a cap, and every use is on the record.

Rules are registered HERE, in code, each for ONE verb with a named predicate over facts ddflow
computes: a rule can never name an arbitrary command. They ship OFF (`[approvals].policy = []`).
A use is recorded as a request answered by `policy:<id>` (never a person) and spent at once
(`engine.record_policy`), so `approvals log` and `doctor` show it; past `policy_max_uses` for the
same item and target the rule steps aside and a person is asked as before."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ...config import Config
from ...core.handlers.asks import asks_of
from .. import gate_accept as GA
from ..ask import engine as E
from . import request as R
from .canon import Canon


@dataclass(frozen=True)
class Rule:
    id: str
    verb: str
    doc: str
    #: (item, target, cfg) -> does the rule's condition hold NOW
    when: Callable[[Any, str, Config], bool]


def _unavailable_enough(it, gate: str, cfg: Config) -> bool:
    runs = [r for r in it.runs_of(gate) if r.outcome == "unavailable"]
    return len(runs) >= cfg.approvals.policy_unavailable_runs


def _measured_baseline_red(it, gate: str, cfg: Config) -> bool:
    return not GA.refusal(it, gate)


RULES: dict[str, Rule] = {
    r.id: r
    for r in (
        Rule(
            "skip_after_unavailable",
            "gate.skip.failed_review",
            "skip a failed review gate whose reviewer was recorded unavailable several times",
            _unavailable_enough,
        ),
        Rule(
            "accept_baseline_red",
            "approve.partial",
            "accept a partial that ddflow measured baseline-red",
            _measured_baseline_red,
        ),
    )
}


def used(state, rule_id: str, subject: str) -> int:
    """How many times ``rule_id`` already approved this subject (item and target)."""
    return sum(
        1
        for r in asks_of(state).values()
        if r["policy"] and r["answered_by"] == f"policy:{rule_id}" and r["subject"] == subject
    )


def apply(log, cfg: Config, state, canon: Canon, *, reason: str = "") -> dict[str, Any] | None:
    """The stamp of an enabled rule that approves ``canon`` now, or None (ask a person)."""
    it = state.items.get(canon.item)
    if it is None:
        return None
    for rule_id in cfg.approvals.policy:
        rule = RULES.get(rule_id)
        if rule is None or rule.verb != canon.verb:
            continue
        if used(state, rule_id, canon.subject()) >= cfg.approvals.policy_max_uses:
            continue
        if not rule.when(it, canon.target, cfg):
            continue
        s = canon.site
        ask_id = E.record_policy(
            log,
            cfg,
            subject=canon.subject(),
            digest=canon.digest(),
            text=R.render(canon, reason, cfg.approvals.reason_chars)
            + f"\n  approved by standing rule: {rule_id}",
            command=E.Command(
                verb=canon.verb,
                tier=s.tier,
                min_level=s.min_level,
                uses=1,
                use_ttl_s=cfg.approvals.use_ttl_s,
                canon=canon.json(),
            ),
            rule_id=rule_id,
            agent=canon.agent,
        )
        return {
            "ask": ask_id,
            "approved_by": f"policy:{rule_id}",
            "via": "policy",
            "level": 1,
            "digest": canon.digest(),
            "proxy": canon.agent,
            "policy": rule_id,
        }
    return None
