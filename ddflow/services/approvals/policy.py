"""Standing approval rules (RA-policy, D-RA-4, D-RA-Q8): an operator who switched a rule on has
decided in advance, within a cap, and every use is on the record.

Rules are registered HERE, in code, each for ONE verb with a named predicate over facts ddflow
computes: a rule can never name an arbitrary command. They ship OFF (`[approvals].policy = []`).
A use is recorded as a request answered by `policy:<id>` (never a person) and spent at once
(`engine.record_policy`), so `approvals log` and `doctor` show it; past `policy_max_uses` for the
same item and target the rule steps aside and a person is asked as before.

The RETRY-THRESHOLD rule (`[approvals].auto_approve_after_retries`, id `auto-after-retries`) is the
one standing rule NOT in `RULES`: it spans the generic T2 verbs (`RETRY_VERBS`), is gated on the
answered+refused COUNT (never the window, never `policy_max_uses`) and always notifies the
operator on every use (`refusals` -> `_approved` with `notify`)."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ...config import Config
from ...core import clock
from ...core.handlers.asks import EXPIRED, PENDING, asks_of
from ...core.records.gates import GateOutcome
from .. import gate_accept as GA
from ..ask import engine as E
from . import request as R
from .canon import Canon

#: The retry-threshold standing rule: recorded as `policy:auto-after-retries`. Not a `RULES`
#: entry (that table is one verb each, window-gated); this one spans the generic T2 class and is
#: gated on the answered+refused COUNT (`[approvals].auto_approve_after_retries`), never the window.
RETRY_RULE_ID = "auto-after-retries"
RETRY_VERBS = ("complete.force", "claim.force", "gate.skip.failed_review", "approve.partial")


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


_UNAVAILABLE_OR_PARTIAL = (GateOutcome.UNAVAILABLE.value, GateOutcome.PARTIAL.value)


def _unavailable_unmet_only(it, target: str, cfg: Config) -> bool:
    """`complete_unavailable`: the completion site names the gates its verdict left unmet in
    ``target`` (comma-separated gate ids, e.g. ``"unit_tests"``), and this predicate checks
    that each is recorded `unavailable` or `partial` (a worktree gone before a gate could run,
    or a command gate that reported partway). WHICH gates are unmet is the verdict's decision
    (a `not_applicable` gate, a chore's skipped gate or a person-accepted partial is NOT
    unmet, and lives in `Verdict.unmet_gates`): this predicate only guarantees that none of
    the gates the verdict flagged is a failure or silence, so a genuinely-failed gate is NEVER
    auto-approved -- the whole point of the rule leaves the unmet facts in one place."""
    gates = [g for g in target.split(",") if g] if target else []
    return bool(gates) and all(it.gate_outcome(g) in _UNAVAILABLE_OR_PARTIAL for g in gates)


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
            _unavailable_unmet_only,
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
        if r["policy"]
        and r["answered_at"]
        and r["verb"] in cfg.approvals.standing_verbs
        and r["answered_by"] != f"policy:{RETRY_RULE_ID}"  # the retry rule never opens the window
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


def refusals(state, canon: Canon) -> int:
    """How many times ``canon``'s verb was filed for its item and left unanswered (the operator
    was asked and did not answer): every ask of that verb for that item whose state is still
    `pending` (or `expired`), counted regardless of which agent asked. A withdrawn or answered
    ask is not a refusal: the asker took it back, or the operator DID answer."""
    item = canon.item
    n = 0
    for r in asks_of(state).values():
        if r["verb"] != canon.verb or r["state"] not in (PENDING, EXPIRED):
            continue
        try:
            asked_item = json.loads(r["canon"]).get("item")
        except (json.JSONDecodeError, TypeError, AttributeError):
            continue
        if asked_item == item:
            n += 1
    return n


def _approved(
    log, cfg: Config, canon: Canon, reason: str, rule_id: str, *, notify: int = 0
) -> dict[str, Any]:
    """Record a standing-rule answer of ``canon`` (reused by the window rules and the retry rule):
    the request, its `policy:<id>` answer and its single spend, with the stamp the effect event
    writes. ``notify`` nonzero flags an operator notification on the answer (the retry rule)."""
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
        notify=notify,
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
        return _approved(log, cfg, canon, reason, rule_id)
    if canon.site.min_level <= 1:  # the retry rule, like every policy answer, never above L1
        threshold = cfg.approvals.auto_approve_after_retries
        if threshold > 0 and canon.verb in RETRY_VERBS:
            n = refusals(state, canon)
            if n >= threshold:
                # COUNT-based, never window-gated, always notifies: record and tell the operator.
                return _approved(log, cfg, canon, reason, RETRY_RULE_ID, notify=n)
    return None
