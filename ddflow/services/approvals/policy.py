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
from ...core.admission import is_shared
from ...core.handlers.asks import EXPIRED, PENDING, asks_of
from ...core.records.gates import COVERAGE_GAP_OUTCOMES, GateOutcome
from .. import gate_accept as GA
from .. import pins as PN
from ..ask import engine as E
from ..gates.defs import required_gates
from ..gates.failed_gate import skipped_after_failure
from . import request as R
from . import sites
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


def _claim_unavailable(it, target: str, cfg: Config) -> bool:
    """`claim_unavailable`: the item is HELD by a lease that has LAPSED -- its holder gone
    (vanished/unavailable, a crashed or dead lane) -- which is exactly the takeover a
    `claim.force` over it performs. The rule is documented for that recovery, never for a
    plain claim the scheduler just refused for its dependencies: a dep-blocked item with NO
    holder, and a LIVE holder, are not "a vanished holder", so the rule must not answer them.
    Its old `_true` predicate answered ANY `claim.force` once the standing window was open, so
    a plain agent claim of a dep-unmet item (refused by `plan_blocker`, re-run as
    `claim.force`) was silently force-granted over its unmet dependencies
    (B-claim-unavailable-fires-past-deps). Requiring a lapsed lease is what stops that: no
    lease -> no answer, a live holder -> no answer."""
    lease = it.lease
    return lease is not None and not lease.live(clock.now_utc().timestamp(), cfg.lease.grace_s)


def _unavailable_enough(it, gate: str, cfg: Config) -> bool:
    runs = [r for r in it.runs_of(gate) if r.outcome == "unavailable"]
    return len(runs) >= cfg.approvals.policy_unavailable_runs


def _measured_baseline_red(it, gate: str, cfg: Config) -> bool:
    """`accept_baseline_red`: ``gate`` is a partial ddflow MEASURED baseline-red, and NOT one only
    a person accepts. A measured partial `gate run` did not auto-accept (its failing tests sit in
    a file the item changed or reaches, or the knob is off; `GA.person_only`) is a person's call
    (`gate_accept.next_step`, D-baseline-red-auto-accept), so the standing rule steps aside for it
    through the SAME helper `complete_unavailable` reads (B7657fc26be)."""
    return not GA.refusal(it, gate) and not GA.person_only(it, gate)


#: The required-gate outcomes a ROUTINE completion may finish over without a person: the ONE
#: enumeration (`core.records.gates.COVERAGE_GAP_OUTCOMES`) the predicate and the completion
#: coverage-gap set both read, never a list spelled here (Bb67dc543b6).
_COVERAGE_GAP_OUTCOMES = COVERAGE_GAP_OUTCOMES


def _unavailable_unmet_only(it, target: str, cfg: Config) -> bool:
    """`complete_unavailable`: `target` -- the FULL `Verdict.unmet_gates` list the
    completion site (`_auto_complete_unavailable`) passes, never a caller-derived subset --
    must be non-empty, and every gate it names a REQUIRED gate recorded `unavailable`
    (a tool could not run), `partial` (it reported partway), or `skipped` (the agent set
    it aside with a recorded reason) -- `_COVERAGE_GAP_OUTCOMES`. A required gate recorded
    `failed` ALWAYS blocks, whatever `target` names (the OVER-FIRE guard below), and a
    never-run (silent) required gate blocks the same way -- its empty outcome is in no
    coverage-gap set, so the outcome check refuses it. An excused required
    gate (`applies_when` not_applicable, a chore's skip, a person-accepted partial) is
    already NOT in `Verdict.unmet_gates`, so it is not in `target` and is not re-judged:
    the verdict alone knows what `applies_when` excused. A `partial` that only a person
    accepts (`GA.person_only`, a measured baseline-red partial `gate run` did not
    auto-accept) is refused here too, so the rule's own predicate and the completion site
    read the SAME helper and cannot drift (B-complete-self-approves-over-person-only-partial)."""
    required = set(required_gates(cfg))
    for g in required:
        if it.gate_outcome(g) == GateOutcome.FAILED.value:
            return False
        # A `skipped` that masked a FAILED run (DX-skip-failed-gate) is a failure wearing a
        # gap's outcome: only the record path that produces it for a required gate is refused
        # today, so this is defense-in-depth, not a reachable state (Bb67dc543b6, roborev).
        if it.gate_outcome(g) == GateOutcome.SKIPPED.value and skipped_after_failure(it, g):
            return False
    names = [g for g in (target or "").split(",") if g]
    return bool(names) and all(
        g in required
        and not it.gate_satisfied(g, True)
        and it.gate_outcome(g) in _COVERAGE_GAP_OUTCOMES
        and not GA.person_only(it, g)
        for g in names
    )


#: The verb of the pins rule. Its canon names the RECIPE in `item` (`--only <recipe>`), not a work
#: item, so the rule resolves the work item itself (`_acting_item`) and `when` reads the recipe.
PINS_VERB = "pins.regenerate"
PINS_RULE_ID = "pins_regenerate"


def _generated_recipe(it, recipe: str, cfg: Config) -> bool:
    """`pins_regenerate`: ``recipe`` is on the operator's allowlist
    (`[approvals].policy_pin_recipes`), is defined in the TRUSTED checkout's recipes file
    (`cfg.root`, the primary), and every file it owns lies inside `[lease].shared_globs`: a recipe
    that rewrites a file the project does not call generated is a person's call."""
    if not recipe or recipe not in cfg.approvals.policy_pin_recipes or cfg.root is None:
        return False
    try:
        found = {r.name: r for r in PN.load_recipes(cfg.root, cfg)}
    except ValueError:  # recipes that do not parse: nothing is trusted
        return False
    r = found.get(recipe)
    shared = list(cfg.lease.shared_globs)
    return r is not None and bool(r.files) and all(is_shared(f, shared) for f in r.files)


def _acting_item(state, canon: Canon, cfg: Config):
    """The ONE work item the acting agent holds a live lease on (None for none or several):
    the item whose use of a pins rule is counted. A rule answers an agent that is working an item,
    never an identity that merely names one."""
    now = clock.now_utc().timestamp()
    held = [
        i
        for i in state.items.values()
        if i.lease is not None
        and i.lease.holder == canon.agent
        and i.lease.live(now, cfg.lease.grace_s)
    ]
    return held[0] if len(held) == 1 else None


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
            _claim_unavailable,
        ),
        Rule(
            PINS_RULE_ID,
            PINS_VERB,
            "regenerate one generated pin recipe on the operator's allowlist, once per recipe "
            "and work item, inside the standing window",
            _generated_recipe,
        ),
    )
}


#: How a standing rule's answer is recorded: `policy:<id>`.
def window_start(state, cfg: Config, *, pins: bool = False) -> float:
    """The start of the standing window: 0.0 before any IN-SCOPE standing rule has fired, else
    the epoch of the LATEST in-scope standing auto-approval's ``answered_at`` -- the most
    recent fire. In-scope means the answer's verb is inside `[approvals].standing_verbs`,
    so a stale answer from a verb the window no longer covers cannot anchor it. ``max``, never
    the first: the window stays open for `[approvals].standing_window_s` after the newest fire,
    re-anchoring on each new one so a later fire keeps it open past the opening fire's lapse
    (the first-fire anchor would leave it permanently closed once `standing_window_s` passed and
    no fire followed). The fold KEEPS every ask row for the life of the log (`use_ttl_s` bounds
    spending, not retention), so the anchor persists past an answer TTL, and
    `approvals log`/`doctor` show what fired within it. The `pins.regenerate` rule has a window
    of its OWN (``pins``): its fires anchor only it and the generic window ignores them, so an
    agent's regenerations can never keep the window of `complete.force` and the like open."""
    starts = [
        clock.epoch(r["answered_at"], default=0.0)
        for r in asks_of(state).values()
        if r["policy"]
        and r["answered_at"]
        and r["verb"] in cfg.approvals.standing_verbs
        and (r["verb"] == PINS_VERB) == pins
        and r["answered_by"] != f"policy:{RETRY_RULE_ID}"  # the retry rule never opens the window
    ]
    return max(starts, default=0.0)


def _window_open(canon: Canon, state, cfg: Config, now: float) -> bool:
    """Is the standing window open now, for ``canon``'s verb?

    Open only when the verb is inside `[approvals].standing_verbs` (the generic T2 scope the
    operator pre-authorised) and either the window has not opened yet -- this fire opens it,
    anchored at ``now`` -- or the most recent in-scope standing fire was fewer than
    `[approvals].standing_window_s` seconds ago."""
    if canon.verb not in cfg.approvals.standing_verbs:
        return False
    start = window_start(state, cfg, pins=canon.verb == PINS_VERB)
    if start <= 0.0:
        return True
    return now < start + cfg.approvals.standing_window_s


def _work_item_line(item_id: str) -> str:
    return f"\n  work item: {item_id}"


def used(state, rule_id: str, subject: str, work_item: str = "") -> int:
    """How many times ``rule_id`` already approved this subject (item and target); with
    ``work_item`` only the uses recorded for that work item (the pins rule, whose subject is the
    recipe alone)."""
    mark = _work_item_line(work_item) if work_item else ""
    return sum(
        1
        for r in asks_of(state).values()
        if r["policy"]
        and r["answered_by"] == f"policy:{rule_id}"
        and r["subject"] == subject
        and (not mark or r["text"].endswith(mark + f"\n  approved by standing rule: {rule_id}"))
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


def retry_armed(cfg: Config, canon: Canon) -> bool:
    """Is the retry-threshold rule armed for ``canon``: `[approvals].auto_approve_after_retries`
    above 0 and ``canon.verb`` a generic T2 retry verb. While armed, the refusal site's re-file of
    an unresolved retry verb must be a NEW filing, not the deduplicated pending one, so
    `refusals` accumulates over distinct filings and the threshold becomes reachable."""
    return cfg.approvals.auto_approve_after_retries > 0 and canon.verb in RETRY_VERBS


def _approved(
    log,
    cfg: Config,
    canon: Canon,
    reason: str,
    rule_id: str,
    *,
    notify: int = 0,
    work_item: str = "",
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
        + (_work_item_line(work_item) if work_item else "")
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


def person_only_target(it, verb: str, target: str) -> bool:
    """Does ``verb`` over ``target`` name a measured baseline-red partial only a person accepts
    (`GA.person_only`)? `approve.partial` targets one gate, `complete.force` the comma-joined
    unmet gates; no other verb accepts a partial. The retry-threshold rule, which answers the
    generic T2 verbs without a per-rule predicate, steps aside for it (B1bc4733e43)."""
    if verb not in ("approve.partial", "complete.force"):
        return False
    return any(GA.person_only(it, g) for g in (target or "").split(",") if g and g in it.gates)


def _rule_stamp(
    log, cfg: Config, state, canon: Canon, it, work: str, reason: str, now: float
) -> dict[str, Any] | None:
    """The stamp of the first enabled `RULES` entry that approves ``canon`` now (``work`` is the
    pins rule's work item: its canon names a recipe, not an item), or None."""
    # a policy answer is a standing decision: never above L1, never a tier-3 act
    if canon.site.min_level > 1 or canon.site.tier == sites.T3:
        return None
    target = canon.item if work else canon.target
    for rule_id in cfg.approvals.policy:
        rule = RULES.get(rule_id)
        if rule is None or rule.verb != canon.verb:
            continue
        if not _window_open(canon, state, cfg, now):
            continue
        if used(state, rule_id, canon.subject(), work) >= cfg.approvals.policy_max_uses:
            continue
        if rule.when(it, target, cfg):
            return _approved(log, cfg, canon, reason, rule_id, work_item=work)
    return None


def apply(
    log, cfg: Config, state, canon: Canon, *, reason: str = "", now: float | None = None
) -> dict[str, Any] | None:
    """The stamp of an enabled rule that approves ``canon`` now, or None (ask a person)."""
    pins = canon.verb == PINS_VERB  # its canon names a recipe, not an item (see `PINS_VERB`)
    it = _acting_item(state, canon, cfg) if pins else state.items.get(canon.item)
    if it is None:
        return None
    now = clock.now_utc().timestamp() if now is None else now
    if stamp := _rule_stamp(log, cfg, state, canon, it, it.id if pins else "", reason, now):
        return stamp
    if pins:  # only its own rule answers a regeneration: never the retry threshold
        return None
    if canon.site.min_level <= 1:  # the retry rule, like every policy answer, never above L1
        threshold = cfg.approvals.auto_approve_after_retries
        if retry_armed(cfg, canon) and not person_only_target(it, canon.verb, canon.target):
            n = refusals(state, canon)
            if n >= threshold:
                # COUNT-based, never window-gated, always notifies: record and tell the operator.
                return _approved(log, cfg, canon, reason, RETRY_RULE_ID, notify=n)
    return None


def record_auto(log, cfg: Config, canon: Canon, text: str, rule_id: str) -> str:
    """Record, in the approvals log, an acceptance ddflow made itself under ``rule_id`` (the
    automatic baseline-red acceptance): answered by `policy:<rule>`, notifying the operator
    in `approvals log` and `doctor`. Returns the ask id."""
    return E.record_policy(
        log,
        cfg,
        subject=canon.subject(),
        digest=canon.digest(),
        text=R.render(canon, text, cfg.approvals.reason_chars)
        + f"\n  accepted automatically: {rule_id}",
        command=R.command_of(canon, cfg),
        rule_id=rule_id,
        agent=canon.agent,
        notify=1,
    )
