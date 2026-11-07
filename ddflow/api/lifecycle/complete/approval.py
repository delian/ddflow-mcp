"""The override policy of `complete`: `--force` is a person's, an agent spends an operator's `--approval`.

Part of `ddflow.api.lifecycle.complete`."""

from __future__ import annotations

from ....core import outcome as O
from ....core.digest import content_digest
from ....services import approval as AP
from ....services import gate_accept as GA
from ....services.approvals import consume as CN
from ....services.approvals.canon import Canon


def _only_unavailable(v, it) -> bool:
    """Would the standing `complete_unavailable` rule answer this completion: its ONLY unmet
    conditions are required gates recorded `unavailable`/`partial`. There must be a single
    blocker (the "required gate(s) not passed" one) and every unmet required gate a coverage
    gap -- never a failure, silence, an open bug or unfinished sub-tasks. Nor a gate whose
    tests FAILED and only their comparison with the base could not run (`baseline.unmeasured`),
    nor a measured baseline-red partial only a person accepts (`gate_accept.person_only`):
    both are a person's call."""
    if not v.unmet_gates or len(v.blockers) != 1:
        return False
    records = [it.gates[g] for g in v.unmet_gates if g in it.gates]
    if any(((r.evidence or {}).get("baseline") or {}).get("unmeasured") for r in records):
        return False
    if any(GA.person_only(it, g) for g in v.unmet_gates if g in it.gates):
        return False
    return set(v.unmet_gates) <= set(v.coverage_gaps)


def _auto_complete_unavailable(log, cfg, it, v, hard, approval, agent, force, stamp):
    """An agent's ROUTINE refusal (no `--approval`) of a completion whose ONLY unmet required
    gates are `unavailable`/`partial` is exactly what the standing `complete_unavailable` rule
    pre-authorised within the window: answer it and return the forced completion's stamp. An
    explicit `--approval request` stays a person ask (Bef8d328af3), and a failure or silence is
    never auto-answered -- `_only_unavailable` and the rule's own predicate both refuse it, so
    this never weakens a genuinely-failed gate."""
    if v.may_complete or force or hard or approval or not _only_unavailable(v, it):
        return force, stamp
    ok, auto_stamp, _why = _approved_force(log, cfg, it.id, v.blockers, "", agent, v.unmet_gates)
    return (True, {"approval": auto_stamp}) if ok else (force, stamp)


def _override_by_approval(log, cfg, item, v, hard, force, approval, agent, base):
    """(force, event fields, refusal): an agent's `--approval` turns an unmet-conditions refusal
    into a recorded override, never over a failed review gate and never for a person."""
    if not (approval and not force and not hard and not v.may_complete):
        return force, {}, None
    ok, stamp, why = _approved_force(log, cfg, item, v.blockers, approval, agent)
    if not ok:
        return force, {}, O.refused("item.completed", why, forced=False, **base)
    return True, {"approval": stamp}, None


def _approved_force(
    log, cfg, item: str, blockers: list[str], approval: str, agent: str, unmet_gates: list[str] = ()
):
    """(force, stamp, refusal) for an agent spending an operator's approval of overriding exactly
    ``blockers`` on ``item`` (verb complete.force). Not filed on every refusal: `--approval request`.
    ``unmet_gates`` names the verdict's unmet required gates so the standing `complete_unavailable`
    predicate checks exactly the gates the verdict flagged, never a re-derived list."""
    if not AP.agent_marker(agent):
        return False, {}, "--approval is an agent's route; a person uses --force"
    canon = Canon(
        verb="complete.force", agent=log.agent_id, item=item, target=",".join(sorted(unmet_gates)),
        state=content_digest("\n".join(sorted(blockers))),
    )  # fmt: skip
    decision = CN.operator_gate(log, cfg, canon, approval_id=approval, auto=False)
    if decision.ok:
        return True, decision.stamp, ""
    # `operator_gate` already prefixes its refusal; adding "refusing: " here stacked it
    # (B-approval-refusal-doubled-prefix). The request text names the verb, not the conditions
    # the approval would override -- those live only in ``blockers`` -- so name them too.
    why = decision.message
    if blockers:
        why = f"{why} It would override: {'; '.join(blockers)}"
    return False, {}, why


def _person_force(force: bool, agent: str) -> bool:
    """B49fa10bbb1: `--force` is a person's. An agent's flag (every MCP call is one) is not
    honoured, whatever it passes beside it: its route is `--approval`, which the verdict spends."""
    return force and not AP.agent_marker(agent)


def _ask_hint(agent: str) -> str:
    """For an agent: how to have the operator override these in chat (it may not use --force)."""
    if not AP.agent_marker(agent):
        return ""
    return (
        "\nAn agent may not use --force; to ask the operator to override these in chat, re-run "
        "with `--approval request`, then `--approval <id>` once they have answered."
    )
