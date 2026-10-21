"""A person accepts one recorded `partial` outcome of a required gate (Bb999d5e20f).

A required gate recorded `partial` (a baseline-red unit_tests, a `partial_exits` command)
blocks `complete`, and until now the only way past was `--force`, which overrides every
blocker and which agents cannot use: so an item whose honest result was partial ended
merged but not completed, and the pressure was to over-record `passed`.

The legitimate paths are two. Re-run the missing part: the new outcome replaces the old.
Or a PERSON runs `ddflow approve <id> <gate>`: an approval (`services.approval`, refused
under an agent identity) of THAT recorded outcome, by digest. Three properties keep it from
becoming a way to turn partial into passed:

* the gate stays `partial` in the record and in `gate status`; completion only stops
  counting it as a blocker, and says so (a warning, and `verify` notes it);
* the digest covers the outcome, its time and its reason, so a re-run or a re-record is a
  different outcome and needs its own acceptance;
* only a `partial` that ddflow MEASURED as baseline-red can be accepted (operator decision
  D-accept-baseline-red-partial): the failing tests also fail on the base branch
  (`services.gates.baseline_red`, evidence `baseline` -> `red` (ev['baseline']['red']), written only by `gate run`: the
  caller's `gate record` cannot set it). `failed`, `unavailable` and a hand-recorded or
  `partial_exits` partial are not this path.
"""

from __future__ import annotations

from ..core.digest import content_digest
from ..core.model import Item, State, fold
from . import approval as AP
from .gates import baseline_red as BR
from .gates.defs import required_gates

ACCEPTABLE = "partial"


def subject(item: str, gate: str) -> str:
    return f"gate-accept:{item}:{gate}"


def digest(it: Item, gate: str) -> str:
    """What a person accepts: this item's current record of ``gate``."""
    rec = it.gates.get(gate)
    if rec is None:
        return ""
    return content_digest("\n".join([it.id, gate, rec.outcome, rec.at, rec.reason]))


def refusal(it: Item, gate: str) -> str:
    """Why ``gate`` of ``it`` cannot be accepted ("" when it can)."""
    got = it.gate_outcome(gate)
    if got != ACCEPTABLE:
        return (
            f"{it.id}.{gate} is {got or 'not run'}, not {ACCEPTABLE}: only a partial outcome "
            f"can be accepted by a person. Re-run the gate, or record it honestly"
        )
    base = (it.gates[gate].evidence or {}).get("baseline")
    if gate not in BR.GATES or not (isinstance(base, dict) and base.get("red") is True):
        return (
            f"{it.id}.{gate} is partial but was not measured baseline-red (the failing tests "
            f"also fail on the base branch): only that partial can be accepted. Re-run the gate"
        )
    return ""


def accepted(state: State, it: Item, gate: str) -> AP.Verdict:
    """Has a person accepted the CURRENT partial outcome of ``gate``?"""
    if refusal(it, gate):
        return AP.Verdict(False, refusal(it, gate))
    return AP.check(state, subject(it.id, gate), digest(it, gate))


def grant(log, it: Item, gate: str, *, note: str = "", requested_agent: str = "") -> AP.Approval:
    """Record the acceptance. Raises `AP.ApprovalRefused` under an agent identity, and
    `ValueError` when the gate's outcome is not a partial."""
    if why := refusal(it, gate):
        raise ValueError(why)
    rec = it.gates[gate]
    return AP.grant(
        log,
        subject(it.id, gate),
        digest(it, gate),
        note=note or f"accepted partial: {rec.reason[:200]}",
        requested_agent=requested_agent,
    )


def approve(
    log, cfg, item_id: str, gate: str, *, note: str = "", reject: bool = False, agent: str = ""
) -> str:
    """`ddflow approve` of a gate that is not a human gate: accept its baseline-red partial.
    Returns the line to print; raises `ValueError` (the CLI prints it) when there is none."""
    it = fold(log.read_all(), strict=False).items.get(item_id)
    if it is None or reject or it.gate_outcome(gate) != ACCEPTABLE:
        raise ValueError(
            f"{gate!r} is not a human-approval gate, so there is nothing for a person "
            f"to approve. Set `[gate.{gate}] human = true` in .ddflow/gates.toml if it "
            f"should be one; otherwise use `ddflow gate record`."
        )
    if gate not in required_gates(cfg):
        raise ValueError(
            f"{gate!r} is not a required gate ([gates].required): a partial outcome of an "
            f"advisory gate blocks nothing, so there is nothing to accept"
        )
    try:
        got = grant(log, it, gate, note=note, requested_agent=agent)
    except AP.ApprovalRefused as exc:
        raise ValueError(str(exc)) from exc
    return (
        f"{it.id}.{gate} partial outcome ACCEPTED by {got.actor.user}; it stays "
        f"partial on the record, and `complete` no longer counts it as a blocker"
    )
