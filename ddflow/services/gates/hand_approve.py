"""Recording a gate outcome, and a person clearing or refusing a human gate.

`record` enforces the evidence contract at the service boundary; `approve` is the
human-approval wrapper around it. Moved out of `outcomes.py` (B92f2fe5750): that module
grew past the module-split cap, and "write an outcome + the human judgement over it" is
one area. `outcomes.py` re-exports `approve`, so `outcomes.approve` keeps working.
"""

from __future__ import annotations

import getpass
from collections.abc import Callable
from typing import Any

from ...config import Config
from ...core.model import GATE_OUTCOMES
from ...infra import hostinfo as H
from ...infra.log import EventLog
from .. import gate_accept as GA
from .. import reviewer_trust as RT
from .defs import GateDef
from .kinds import bare, evidence_problems


def record(  # noqa: PLR0913 -- the caller's evidence and ddflow's measurements are kept apart on purpose
    log: EventLog,
    cfg: Config,
    item_id: str,
    gate: str,
    outcome: str,
    *,
    by: str = "",
    reason: str = "",
    evidence: dict[str, Any] | None = None,
    gates: dict[str, GateDef] | None = None,
    human: bool = False,
    measured: dict[str, Any] | None = None,
    owner_of: Callable[[str], str | None] | None = None,
    forced: bool = False,
) -> None:
    """Write a gate outcome to the log, enforcing the evidence contract.

    ``evidence`` is what the CALLER supplied; ``measured`` is what ddflow determined
    itself (the tree fingerprint, the diff size). Only the first can satisfy the
    contract: the measured fields are always present, so counting them made every bare
    pass look evidenced (bug Bbc9a7ee3f2). Both are recorded.

    Rejecting a bare pass at the API boundary is deliberate. If the only thing standing
    between "I ran the tests" and a recorded pass is the agent's honesty, then over a
    long run the record measures honesty rather than testing.
    """
    if outcome not in GATE_OUTCOMES:
        raise ValueError(f"bad outcome {outcome!r}; expected one of {GATE_OUTCOMES}")
    gdef = (gates or {}).get(gate)
    # Refused HERE, at the service boundary, not in the CLI branch that happens to be
    # the usual caller. A check that lives in one surface is a check the other surface
    # does not have, which is how `ddflow_gate_record` would have cleared a human
    # checkpoint over MCP while the terminal refused it.
    if gdef is not None and gdef.is_human_gate and not human:
        raise ValueError(
            f"{gate!r} is a human-approval gate: it is cleared by a person, not by an "
            f"agent recording that it happened. Ask the operator to run "
            f"`ddflow approve {item_id} {gate}` (or `--reject --reason ...`). "
            f"There is deliberately no MCP tool for this."
        )
    if evidence and evidence.get("reviewer") and not evidence.get("reviewer_digest"):
        # WHICH entry reviewed, as configured right now: what reviewer independence
        # checks against `reviewer.configured`/`reviewer.approved` (D-reviewer-trust).
        # Only `ddflow review` writes a `reviewer` key; `gate record` cannot.

        if dig := RT.digest_of(log.root, str(evidence["reviewer"])):
            evidence = {**evidence, "reviewer_digest": dig}
    if outcome == "skipped":
        if not cfg.gates.allow_skip_with_reason:
            raise ValueError("skipping is disabled ([gates].allow_skip_with_reason)")
        if not reason:
            raise ValueError("a skip must carry --reason; an unexplained skip is invisible")
    if outcome == "passed" and gdef and gdef.evidence and bare(evidence):
        raise ValueError(
            f"gate {gate!r} requires evidence to pass (it is in gates.evidence_required). "
            f"Attach what you observed: --evidence '<what you ran / what it said>', and/or "
            f"--command '<the command>' --exit-code N --output-file <its saved output> "
            f"(over MCP: evidence, command, exit_code, output_file) — or record "
            f"`--outcome unavailable --reason '<why>'`, which is an honest result."
        )
    if outcome == "passed" and (why := evidence_problems(gdef, evidence or {}, item_id, owner_of)):
        raise ValueError(
            f"gate {gate!r} cannot pass without {'; '.join(why)}. Record "
            f"`unavailable` with a reason if the check could not run, or skip it with one."
        )
    if outcome in ("unavailable", "partial", "failed") and not reason:
        raise ValueError(f"outcome {outcome!r} must carry a --reason")
    log.append(
        f"gate.{outcome}",
        item_id,
        {
            "gate": gate,
            "by": by or log.agent_id,
            "reason": reason,
            "evidence": {**(measured or {}), **(evidence or {})},
            # Written over another clone's live lease with --force (B90b43dd2d8); additive.
            **({"forced": True} if forced else {}),
        },
    )


def approve(
    log: EventLog,
    cfg: Config,
    item_id: str,
    gate: str,
    *,
    gates: dict[str, GateDef] | None = None,
    note: str = "",
    reject: bool = False,
    reason: str = "",
    requested_agent: str = "",
    approval: str = "",
) -> str:
    """A PERSON clears (or refuses) a human gate. Returns the line to print.

    Records the OS user rather than the agent id, and stamps `human: true` on the
    evidence. Neither makes forgery impossible — an agent with a shell can run this —
    but both make a forged approval *visible* in the log instead of identical to a real
    one, which is the difference between a record you can audit and one you cannot.

    A rejection is a first-class outcome, not the absence of an approval: "the operator
    looked and said no" and "nobody has looked yet" are different states, and an item
    sitting in the second forever is how a checkpoint becomes a silent stall.
    """
    gdef = (gates or {}).get(gate)
    if gdef is None:
        raise ValueError(f"no such gate {gate!r}")
    if not gdef.is_human_gate:
        return GA.approve(
            log,
            cfg,
            item_id,
            gate,
            note=note,
            reject=reject,
            agent=requested_agent,
            approval=approval,
        )
    if reject and not reason:
        raise ValueError("a rejection must carry --reason: 'no' with no reason cannot be acted on")

    try:
        who = getpass.getuser()
    except Exception:
        # Not fatal, and not silently blank: an approval whose approver is unknown is
        # still a real approval, and saying "unknown" is honest where inventing a name
        # would not be.
        who = "unknown-user"
    ev = {
        "human": True,
        "approved_by": who,
        "host": H.short_host(),
        "note": note,
    }
    outcome = "failed" if reject else "passed"
    record(
        log,
        cfg,
        item_id,
        gate,
        outcome,
        by=who,
        reason=reason,
        evidence=ev,
        gates=gates,
        human=True,
    )
    verb = "REJECTED" if reject else "approved"
    tail = f" — {reason}" if reason else (f" — {note}" if note else "")
    return f"{item_id}.{gate} {verb} by {who}{tail}"
