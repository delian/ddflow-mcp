"""The batch record and the fan-out of one batch gate run (BG-record, D-BG-3).

A batch is a group of items verified by ONE gate run. This module writes its events
(`batch.formed`, `batch.gate_started|finished`, `batch.culprit`, `batch.landed`,
`batch.dissolved`) and fans a gate outcome out to every member's own ordinary
`gate.<outcome>` event with evidence ``{batch, batch_gate_run, covered_by: "batch", members,
tree_sha}``. The fan-out is ONE write: every event is checked first and appended together, so a
member whose outcome cannot be recorded stops all of them. A gate that did not run is
``unavailable`` for every member, never ``passed``. Nothing here decides who is in a batch
(``batch_trigger``), what a company allows (``[batch]``) or how a red batch is searched
(``batch_culprit``): it records what they decided.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ..adapters import digest as DG
from ..config import Config
from ..core.model import GATE_OUTCOMES, fold
from ..infra.log import EventLog
from .gates.defs import GateDef, load_gates
from .gates.kinds import bare, evidence_problems

#: Evidence ``covered_by`` value on a member's outcome: the batch run stood in for its own.
COVERED_BY = "batch"


def _batches(log: EventLog) -> dict[str, dict[str, Any]]:
    return fold(log.read_all(), strict=False).batches


def _open(batches: Mapping[str, dict[str, Any]], batch: str) -> dict[str, Any]:
    b = batches.get(batch)
    if b is None:
        raise ValueError(f"no such batch {batch!r}")
    if b["state"] != "formed":
        raise ValueError(f"batch {batch} is already {b['state']}")
    return b


def form(
    log: EventLog,
    batch: str,
    members: Sequence[Mapping[str, Any]],
    *,
    trigger: str,
    base_sha: str,
) -> None:
    """Record ``batch`` formed from ``members`` (``{item, tip, paths}`` each).

    Refused: an id already used, no member, a member twice, or a member already in a batch that
    has not landed or dissolved (an item is verified by one batch at a time)."""
    rows = []
    for m in members:
        if not m.get("tip"):
            raise ValueError(f"member {m.get('item')!r} has no tip sha")
        if not isinstance(m.get("paths"), (list, tuple)):
            raise ValueError(
                f"member {m.get('item')!r} has no paths manifest (a list, maybe empty)"
            )
        rows.append({"item": str(m["item"]), "tip": str(m["tip"]), "paths": list(m["paths"])})
    if not rows:
        raise ValueError("a batch needs at least one member")
    ids = [r["item"] for r in rows]
    if len(set(ids)) != len(ids):
        raise ValueError(f"a member appears twice in batch {batch}: {sorted(ids)}")
    with log.transaction():
        have = _batches(log)
        if batch in have:
            raise ValueError(f"batch {batch} already exists")
        for other in have.values():
            busy = (
                {m["item"] for m in other["members"]} & set(ids)
                if other["state"] == "formed"
                else set()
            )
            if busy:
                raise ValueError(f"{sorted(busy)[0]} is already in batch {other['id']}")
        log.append("batch.formed", batch, {"members": rows, "trigger": trigger, "base": base_sha})


def gate_started(
    log: EventLog, batch: str, *, gate: str, tree_sha: str, command: str = "", run: str = ""
) -> str:
    """Record the start of a batch gate run; returns the run id (made from the batch, gate and
    tree when not given) that ``fan_out`` closes."""
    run = run or _run_id(batch, gate, tree_sha)
    with log.transaction():
        _open(_batches(log), batch)
        log.append(
            "batch.gate_started",
            batch,
            {
                "gate": gate,
                "tree_sha": tree_sha,
                "run": run,
                "command_digest": DG.hasher(command.encode()).hexdigest()[:16] if command else "",
            },
        )
    return run


def _run_id(batch: str, gate: str, tree_sha: str) -> str:
    return DG.hasher(f"{batch}\0{gate}\0{tree_sha}".encode()).hexdigest()[:12]


def fan_out(  # noqa: PLR0913 -- one keyword per fact of a gate run; all of them recorded
    log: EventLog,
    cfg: Config,
    batch: str,
    *,
    gate: str,
    outcome: str,
    tree_sha: str,
    by: str = "",
    reason: str = "",
    evidence: Mapping[str, Any] | None = None,
    elapsed_s: float | None = None,
    queue_wait_s: float | None = None,
    run: str = "",
    member_outcomes: Mapping[str, tuple[str, str]] | None = None,
    gates: Mapping[str, GateDef] | None = None,
) -> str:
    """Record the outcome of ``gate`` on the batch's tree for EVERY member, in one write.

    ``member_outcomes`` overrides single members (``item -> (outcome, reason)``): a culprit is
    ``failed`` while the rest keep the batch's outcome. Returns the run id. Raises ValueError,
    writing nothing, for an unknown or closed batch, a member outside the batch, a bad outcome,
    a non-pass without a reason, a pass without evidence where the gate requires some, or a
    human gate (a person clears those, never a batch)."""
    run = run or _run_id(batch, gate, tree_sha)
    over = dict(member_outcomes or {})
    with log.transaction():
        b = _open(_batches(log), batch)
        items = [m["item"] for m in b["members"]]
        for stray in sorted(set(over) - set(items)):
            raise ValueError(f"{stray} is not a member of batch {batch}")
        gdef = (gates if gates is not None else load_gates(log.root, cfg)).get(gate)
        if gdef is not None and gdef.is_human_gate:
            raise ValueError(f"{gate!r} is a human-approval gate: a batch never clears it")
        if outcome != "unavailable" and not any(
            g["run"] == run
            and not g.get("finished")
            and g["gate"] == gate
            and g["tree_sha"] == tree_sha
            for g in b["gates"]
        ):
            raise ValueError(
                f"batch {batch}: run {run} of {gate} on tree {tree_sha} was never started "
                "(batch gate_started); a gate that did not run is unavailable, never "
                f"{outcome}"
            )
        if over and set(over) >= set(items):
            raise ValueError(
                f"batch {batch}: the member outcomes cover every member; record the batch "
                f"outcome itself ({outcome} would leave all of them failed)"
            )
        base_ev = {
            **(evidence or {}),
            "covered_by": COVERED_BY,
            "batch": batch,
            "batch_gate_run": run,
            "members": items,
            "tree_sha": tree_sha,
        }
        events: list[tuple[str, str, dict[str, Any]]] = [
            (
                "batch.gate_finished",
                batch,
                _finished(gate, outcome, tree_sha, run, elapsed_s, queue_wait_s, reason, items),
            )
        ]
        for item in items:
            out, why = over.get(item, (outcome, reason))
            _check(cfg, gdef, gate, out, why, base_ev, item, evidence)
            events.append(
                (
                    f"gate.{out}",
                    item,
                    {"gate": gate, "by": by or log.agent_id, "reason": why, "evidence": base_ev},
                )
            )
        _check(
            cfg, gdef, gate, outcome, reason, base_ev, batch, evidence
        )  # the batch's own outcome too
        log.append_many(events)
    return run


def _finished(gate, outcome, tree_sha, run, elapsed_s, queue_wait_s, reason, items):
    d: dict[str, Any] = {
        "gate": gate,
        "outcome": outcome,
        "tree_sha": tree_sha,
        "run": run,
        "reason": reason,
        "members": items,
    }
    if elapsed_s is not None:
        d["elapsed_s"] = elapsed_s
    if queue_wait_s is not None:
        d["queue_wait_s"] = queue_wait_s
    return d


def _check(cfg, gdef, gate, outcome, reason, evidence, item, given=None) -> None:
    """The evidence contract of ``gates.outcomes.record``, checked before anything is written."""
    if outcome not in GATE_OUTCOMES:
        raise ValueError(f"bad outcome {outcome!r}; expected one of {GATE_OUTCOMES}")
    if outcome in ("unavailable", "partial", "failed") and not reason:
        raise ValueError(f"outcome {outcome!r} for {item} must carry a reason")
    if outcome == "skipped":
        raise ValueError("a batch never skips a gate for its members: skips are per item")
    if outcome == "passed" and gdef is not None:
        if gdef.evidence and bare(given):
            raise ValueError(f"gate {gate!r} requires evidence to pass")
        if why := evidence_problems(gdef, evidence, item, None):
            raise ValueError(f"gate {gate!r} cannot pass without {'; '.join(why)}")


def culprit(log: EventLog, batch: str, *, member: str, runs: int) -> None:
    """Record the member the culprit search named and how many runs it took."""
    with log.transaction():
        b = _open(_batches(log), batch)
        if member not in [m["item"] for m in b["members"]]:
            raise ValueError(f"{member} is not a member of batch {batch}")
        log.append("batch.culprit", batch, {"member": member, "runs": int(runs)})


def landed(log: EventLog, batch: str, *, sha: str) -> None:
    """Record that the batch (its remaining members) merged as ``sha``."""
    with log.transaction():
        _open(_batches(log), batch)
        log.append("batch.landed", batch, {"sha": sha})


def dissolved(log: EventLog, batch: str, *, reason: str) -> None:
    """Record that the batch was given up: its members go back to per-item merges."""
    if not reason:
        raise ValueError("a dissolved batch carries its reason")
    with log.transaction():
        _open(_batches(log), batch)
        log.append("batch.dissolved", batch, {"reason": reason})
