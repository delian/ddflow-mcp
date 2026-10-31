"""Record a gate outcome an agent produced, or skip the gate on the record."""

from __future__ import annotations

from pathlib import Path

from ...core import outcome as O
from ...services import gates as G
from ...services.gates import handrecord as HR
from ...services.gates import measured as R
from ...services.identity import agent_marker
from ..gate_guard import guard_record
from ._query import _order_gate, _resolve
from ._tree import item_tree
from ._vet import (
    Evidence,
    _agent_skip_refusal,
    _docs_gate_export,
    _evidence_fields,
    _has_research_note,
    _implement_without_commits,
    _not_gated_skip_note,
    _override_gate,
    _roborev_reviewer,
    _vet_claims,
)


def record(  # noqa: PLR0913 -- one keyword per facet of a gate record; all keyword-only
    repo: Path,
    item: str,
    gate: str,
    *,
    outcome: str = "",
    skip: bool = False,
    reason: str = "",
    evidence: Evidence | None = None,
    agent: str = "",
    called_from: Path | None = None,
    force: bool = False,
    approval: str = "",
) -> O.Outcome:
    """Record an outcome an agent produced, or skip the gate on the record.

    One function for both because they differ in exactly two places — the outcome is
    forced to `skipped`, and a skip records no magnitude, since nothing was reviewed and
    a diff size would imply an inspection that did not happen.
    """

    # `None`, not a shared default instance: a mutable default is one object for every
    # call, and `Evidence` is a dataclass somebody will add a list field to.
    evidence = evidence or Evidence()
    resolved = _resolve(repo, item, gate, agent)
    if isinstance(resolved, O.Outcome):
        return resolved
    log, cfg, st, it, gdef, gates = resolved

    refused, approval_stamp = _agent_skip_refusal(
        it,
        gdef,
        gate,
        item,
        agent,
        hard_skip := skip or outcome == "skipped",
        outcome != "failed",
        G.required_gates(cfg, gates),
        gated=_override_gate(log, cfg, it, gate, item, (skip, outcome), approval, reason),
    )
    if refused is not None:
        return refused
    # B97106be46a: a refused skip already says which case it is (the refusal names the
    # request id); a skip that was NOT refused must say so too. See `_not_gated_skip_note`.

    # Recording over another clone's live lease replaces what its holder recorded (last
    # event wins): `--force` with the reason the record carries anyway (B90b43dd2d8).
    refusal, forced_by = guard_record(
        log, cfg, st, it, force=force, reason=reason, what=f"recording {gate}"
    )
    if refusal:
        return O.refused("gate.record", refusal, id=item, gate=gate, outcome="")

    ordered = _order_gate(log, cfg, st, item, gate, recording=True, defs=gates, repo=repo)
    if isinstance(ordered, O.Outcome):
        return ordered
    # The `warn` note. Carried rather than printed, so the surface decides where it goes
    # -- and so it is not silently lost, which is exactly what happened when this moved
    # out of `cmd_gate`: the api computed `ahead` and threw it away, and
    # `test_recording_a_gate_out_of_order_says_so` caught it on the full suite.
    warning = R.Order(ordered).note(gate, cfg.gates.enforce_order)
    warning = _not_gated_skip_note(
        warning,
        approval_stamp,
        it,
        gdef,
        gate,
        hard_skip,
    )

    result = "skipped" if skip else outcome
    ev, bad = _evidence_fields(evidence, item, gate, approval_stamp)
    if bad is not None:
        return bad

    warning = _docs_gate_export(
        repo,
        cfg,
        ev,
        warning,
        applies=not skip and (gate, it.kind, result) == ("docs", "phase", "passed"),
    )

    wt: Path | None = None
    if not skip:
        # WHICH tree and HOW MUCH, for AGENT gates too. These were added to
        # `run_command_gate` only, so they never reached the gates the rationale was
        # written about: "a review gate that passed over 4,000 changed lines in two
        # minutes is a different claim from one that passed over 12" -- and `rubber_duck`,
        # `critic` and `standards` are all agent gates recorded through this branch. The
        # feature missed its own motivating case.
        # Where the item's work is (B8be9373cf5): from its worktree, not the primary.
        wt, held = item_tree(repo, cfg, st, it, called_from)
        if held:
            # Recorded all the same: an agent gate is the agent's assertion, and an
            # orchestrator records gates for many items from wherever it stands. Only the
            # MEASUREMENT is ddflow's, and it is not taken from another item's tree.
            warning = " ".join(
                filter(
                    None,
                    [
                        warning,
                        f"NOTE: you are in {held}'s worktree, so nothing was measured for {item}.",
                    ],
                )
            )
        # MEASURED, and passed apart from what the caller supplied: merged into `ev`
        # they made every bare pass look evidenced (bug Bbc9a7ee3f2). Nothing, rather
        # than another item's tree, when the caller stands in one.

    # A command gate's pass is `gate run`'s to give (Bed643a611b); and what a hand record
    # could not measure is said in it (Bb305b43f56).
    why, extra = HR.hand_record(
        it, gdef, gate, result, agent_marker(agent), wt, evidence, log.agent_id, skip=skip
    )
    if why:
        return O.refused("gate.record", why, id=item, gate=gate, outcome=result)
    ev.update(extra)

    vetted = _vet_claims(repo, cfg, st, log, it, gdef, gate, wt, evidence, skip)
    if vetted.refusal:
        return O.refused("gate.record", vetted.refusal, id=item, gate=gate, outcome=result)
    if not force and (
        why := _implement_without_commits(
            repo, cfg, it, gate, result, wt, _has_research_note(st, it)
        )
    ):
        return O.refused("gate.record", why, id=item, gate=gate, outcome=result)
    ev.update(vetted.evidence)
    warning = " ".join(filter(None, [warning, vetted.note]))
    by, note = _roborev_reviewer(repo, wt, gate, ev, evidence.model, skip=skip, gdef=gdef)
    warning = " ".join(filter(None, [warning, note]))

    warning = " ".join(filter(None, [warning, G.untriaged_note(it, gate)]))
    try:
        R.record_measured(
            log,
            cfg,
            repo,
            it,
            gate,
            result,
            reason=reason,
            evidence=ev or None,
            gates=gates,
            by=by,
            tree=wt,
            owner_of=G.record_owner(st),
            forced=bool(forced_by),
        )
    except ValueError as exc:
        if gdef is not None and gdef.is_human_gate:
            return O.refused("gate.record", str(exc), id=item, gate=gate, outcome=result)
        return O.failed("gate.record", str(exc), id=item, gate=gate, outcome=result)
    return O.ok("gate.record", id=item, gate=gate, outcome=result, ahead=ordered, warning=warning)
