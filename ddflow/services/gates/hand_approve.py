"""A PERSON clears or refuses a human gate (`ddflow gate approve`).

Moved out of `outcomes.py` (B92f2fe5750): that module grew past the module-split cap,
and `approve` (with the record it wraps) is one area.
"""

from __future__ import annotations

import getpass

from ...config import Config
from ...infra import hostinfo as H
from ...infra.log import EventLog
from .. import gate_accept as GA
from .defs import GateDef
from .outcomes import record


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
