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
from ...core import clock
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


def _true(_it, _target: str, _cfg: Config) -> bool:
    """A generic-verb rule whose only item-level gate is the site digest: the outstanding-window
    and `standing_verbs` checks `apply` makes are its whole predicate."""
    return True


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
        Rule(
            "complete_unavailable",
            "complete.force",
            "complete an item whose unmet completion condition is an unavailable tool/gate, "
            "inside the standing window",
            _true,
        ),
        Rule(
            "claim_unavailable",
            "claim.force",
            "claim an item held by a vanished/unavailable holder, inside the standing window",
            _true,
        ),
    )
}


#: How a standing rule's answer is recorded: `policy:<id>`.
def window_start(state, cfg: Config) -> float:
    """The start of the standing window: 0.0 before any IN-SCOPE standing rule has fired, else
    the epoch of the FIRST in-scope standing auto-approval's ``answered_at`` -- the fire that
    OPENED the window. In-scope means the answer's verb is inside `[approvals].standing_verbs`,
    so a stale answer from a verb the window no longer covers cannot anchor it. ``min``, never
    the latest: the window never rolls forward, so `[approvals].standing_window_s` is a hard
    bound even while uses keep landing. The fold KEEPS every ask row for the life of the log
    (`use_ttl_s` bounds spending, not retention), so the anchor persists past an answer TTL, and
    `approvals log`/`doctor` show what fired within it."""
    starts = [
        clock.epoch(r["answered_at"], default=0.0)
        for r in asks_of(state).values()
        if r["policy"] and r["answered_at"] and r["verb"] in cfg.approvals.standing_verbs
    ]
    return min(starts, default=0.0)


def _window_open(canon: Canon, state, cfg: Config, now: float) -> bool:
    """Is the standing window open now, for ``canon``'s verb?

    Open only when the verb is inside `[approvals].standing_verbs` (the generic T2 scope the
    operator pre-authorised) and either the window has not opened yet -- this fire opens it,
    anchored at ``now`` -- or it opened fewer than `[approvals].standing_window_s` seconds ago."""
    if canon.verb not in cfg.approvals.standing_verbs:
        return False
    start = window_start(state, cfg)
    if start <= 0.0:
        return True
    return now < start + cfg.approvals.standing_window_s


def used(state, rule_id: str, subject: str) -> int:
    """How many times ``rule_id`` already approved this subject (item and target)."""
    return sum(
        1
        for r in asks_of(state).values()
        if r["policy"] and r["answered_by"] == f"policy:{rule_id}" and r["subject"] == subject
    )


def apply(
    log, cfg: Config, state, canon: Canon, *, reason: str = "", now: float | None = None
) -> dict[str, Any] | None:
    """The stamp of an enabled rule that approves ``canon`` now, or None (ask a person)."""
    it = state.items.get(canon.item)
    if it is None:
        return None
    now = clock.now_utc().timestamp() if now is None else now
    for rule_id in cfg.approvals.policy:
        rule = RULES.get(rule_id)
        if rule is None or rule.verb != canon.verb:
            continue
        if canon.site.min_level > 1:  # a policy answer is a standing decision, never above L1
            continue
        if not _window_open(canon, state, cfg, now):
            continue
        if used(state, rule_id, canon.subject()) >= cfg.approvals.policy_max_uses:
            continue
        if not rule.when(it, canon.target, cfg):
            continue
        ask_id = E.record_policy(
            log,
            cfg,
            subject=canon.subject(),
            digest=canon.digest(),
            text=R.render(canon, reason, cfg.approvals.reason_chars)
            + f"\n  approved by standing rule: {rule_id}",
            command=R.command_of(canon, cfg),
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
