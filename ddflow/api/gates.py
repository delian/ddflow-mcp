"""The gate pipeline: ask what is next, run it, record it, skip it, prove it can fail.

This family is the clearest case B36 was filed about. `cmd_gate` was 192 lines and it WAS
the rule-set: pipeline-order enforcement and its three policies, the human-gate refusal,
the agent-gate refusal, evidence assembly, the out-of-order event, and the mapping from a
gate outcome to an exit code. All of it reachable only through `main(argv)`, so none of it
was callable or testable without building an argparse Namespace.

Two rules here are worth reading before changing anything:

* **Order is enforced but not by default.** `gates.enforce_order` is `warn`, and a
  recording out of order appends a `gate.out_of_order` event either way. Reported rather
  than refused because some interleaving is legitimate, and a tool that blocks on every
  harmless reordering gets `--force`d on reflex. The event exists so that whether `warn`
  should become `block` has evidence behind it.
* **A human gate is a REFUSAL (3), never a failure (1).** Nothing went wrong; the caller
  is simply not the party who can clear it. An agent branching on exit codes needs to
  tell "this is broken" from "this is not yours to do".
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..core import outcome as O
from ..core.plain import plain
from ..services import gates as G
from ._base import _load

#: A command gate's outcome -> the exit code the caller sees. `unavailable` and `partial`
#: are 2: the gate could not report, which is not a pass and not a failure of the work.
OUTCOME_EXIT = {
    "passed": O.OK,
    "skipped": O.OK,
    "failed": O.FAIL,
    "unavailable": O.NOTHING,
    "partial": O.NOTHING,
}


def _gates_ahead_of(st, cfg, item_id: str, gate: str) -> list[str]:
    """Pipeline gates BEFORE ``gate`` that have no outcome yet.

    The order in `gates.task_pipeline` is not decoration: a rubber-duck review recorded
    before `implement` reviewed an empty diff, and a `merge` recorded before `unit_tests`
    merged something nobody tested.
    """
    try:
        s = G.status(st, cfg, item_id)
    except KeyError:
        return []
    if gate not in s.pipeline:
        return []
    before = s.pipeline[: s.pipeline.index(gate)]
    it = st.items.get(item_id)
    return [g for g in before if it and not it.gate_outcome(g)]


def _order_note(ahead: list[str], gate: str) -> str:
    return (
        f"{gate} comes after {', '.join(ahead)} in the pipeline, and "
        f"{'none of those have' if len(ahead) > 1 else 'that one has not'} run yet."
    )


def _resolve(repo: Path, item: str, gate: str, agent: str):
    """(log, cfg, state, item, gatedef) or an Outcome explaining why not.

    One place for the two existence checks every gate operation needs. `approve` once
    skipped the item check and a typo'd id MATERIALISED an item carrying a human
    approval, because the fold creates an item for an unknown subject.
    """
    log, cfg, st = _load(repo, agent)
    known = G.load_gates(repo, cfg)
    if gate and gate not in known:
        return O.failed(
            "gate", f"unknown gate {gate!r}; known: {', '.join(sorted(known))}", gate=gate
        )
    it = st.items.get(item)
    if it is None or it.removed:
        gone = " (it was removed from the queue)" if it is not None else ""
        return O.failed("gate", f"no such item {item!r}{gone}", id=item, gate=gate)
    return log, cfg, st, it, known.get(gate), known


def status(repo: Path, item: str, *, agent: str = "") -> O.Outcome:
    """What has run, what is next, and the INSTRUCTION for the next gate.

    The instruction is the useful half — it is what tells an agent what the gate expects
    and how to record it — which is why this tool's body is prose on both surfaces.
    """
    from ..services import prompts as P

    _log, cfg, st = _load(repo, agent)
    try:
        s = G.status(st, cfg, item)
    except KeyError:
        return O.failed("gate.status", f"no such item {item!r}", id=item, text="")

    gates = G.load_gates(repo, cfg)
    gd = gates.get(s.current)
    lines = [f"{item}: {'COMPLETE' if s.complete else 'next = ' + (s.current or '—')}", s.render()]
    if gd:
        try:
            lines.append(
                "\n"
                + P.render(
                    P.resolve("gate_instruction", repo, P.overrides_from(cfg)), gate=gd, item=item
                )
            )
        except P.TemplateError as exc:
            # The gate is still named and still has a prompt; losing the FORMATTING of
            # the instruction must not lose the instruction.
            lines.append(f"\n{gd.title}: {gd.prompt}   [template error: {exc}]")
    if s.unavailable:
        lines.append(
            f"\n  NOTE: {', '.join(s.unavailable)} did not run. "
            f"That is a gap in coverage, not a pass."
        )
    return O.ok("gate.status", id=item, status=plain(s), text="\n".join(lines))


def verify(repo: Path, item: str, gate: str, *, agent: str = "") -> O.Outcome:
    """Can this gate go red at all? Applies a known-bad mutation and restores the tree.

    The only gate operation that WRITES to the working tree. Asked BEFORE the
    pipeline-order check, deliberately: this is a question about the GATE's definition,
    not about the item's progress, and with `enforce_order = "block"` it was refused for
    any gate whose predecessors had not run — which is every gate at the moment you most
    want to know the answer.
    """
    resolved = _resolve(repo, item, gate, agent)
    if isinstance(resolved, O.Outcome):
        return resolved
    _log, cfg, st, it, gdef, gates = resolved

    results, reason = G.verify(st, cfg, gates, gate, repo, it)
    data: dict[str, Any] = {
        "gate": gate,
        "reason": reason,
        "results": [
            {"file": r.file, "applied": r.applied, "detected": r.detected, "detail": r.detail}
            for r in results
        ],
        # ALL THREE clauses. `results` is empty on every pre-flight failure -- unknown
        # gate, agent gate, no registered mutations, no green baseline -- and `all([])` is
        # True, so scoring on `results` alone turns each of those into a pass.
        "verified": bool(results) and not reason and all(r.ok for r in results),
    }
    # Built explicitly rather than through `O.failed(kind, reason, **data)`, because
    # `reason` is ALSO a wire field here -- `gate verify --json` has always carried the
    # pre-flight explanation under that name -- and the helper takes its own `reason`
    # positionally, so spreading `data` passed it twice and raised TypeError. Loudly, and
    # only on the failing path, which is the one nobody exercises by accident.
    if data["verified"]:
        return O.Outcome(kind="gate.verify", data=data)
    # A HUMAN gate is not unverifiable-because-broken: there is nothing a mutation could
    # demonstrate about whether a person looked. That is a coordination refusal.
    if gdef is not None and gdef.is_human_gate:
        return O.Outcome(
            kind="gate.verify",
            data=data,
            exit=O.REFUSED,
            reason=reason
            or f"{gate} is a human-approval gate; a mutation cannot prove a person looked",
        )
    return O.Outcome(
        kind="gate.verify",
        data=data,
        exit=O.FAIL,
        reason=reason
        or (
            f"{gate} did NOT catch every mutation. A gate that cannot fail is worse than "
            f"no gate — it reports success on every change and everyone downstream reads "
            f"that as evidence."
        ),
    )


def _order_gate(log, cfg, st, item: str, gate: str, *, recording: bool) -> O.Outcome | list[str]:
    """Enforce `gates.enforce_order`, and RECORD the violation either way."""
    ahead = _gates_ahead_of(st, cfg, item, gate)
    if not ahead or cfg.gates.enforce_order == "off":
        return []
    if cfg.gates.enforce_order == "block":
        return O.refused(
            "gate",
            f"{_order_note(ahead, gate)}\nThe order is the point: reviewing a change "
            f"before it is implemented reviews nothing. Run them in order, or set "
            f"[gates].enforce_order = 'warn'.",
            id=item,
            gate=gate,
            ahead=ahead,
        )
    if recording:
        # Recorded, not just printed. Whether "warn" should become "block" or "off" is a
        # judgement about how often this fires, and for as long as it only ever printed,
        # that judgement had no evidence behind it either way.
        log.append(
            "gate.out_of_order",
            item,
            {"gate": gate, "ahead": ahead, "policy": cfg.gates.enforce_order},
        )
    return ahead


def _lease_keeper(log, cfg, it) -> Callable[[], None] | None:
    """A callback renewing the caller's lease on `it`, or None if the caller holds none.

    A full test suite outlives a lease (25 minutes against 1800 s on the project this
    was built beside), and over MCP the caller cannot heartbeat while it is blocked in
    the call. Called between polls of the running command, on this thread -- see
    `gates._run_ticking` for why not a background thread. A failed renewal must not kill
    the gate, so it is swallowed; the lease then expires as it would have anyway.
    """
    from ..services import leases as L

    if not (it.lease and it.lease.holder == log.agent_id):
        return None
    holder = it.lease.holder

    def renew() -> None:
        try:
            L.renew(log, it.id, holder)
        except Exception:
            pass

    return renew


def run(repo: Path, item: str, gate: str, *, agent: str = "") -> O.Outcome:
    """Execute a command gate and record what it said.

    Refuses a human gate (nothing to run) and an agent gate (ddflow cannot perform it)
    with exit 2 and the instruction, rather than pretending to have run something.
    """
    from ..infra import worktree as W

    resolved = _resolve(repo, item, gate, agent)
    if isinstance(resolved, O.Outcome):
        return resolved
    log, cfg, st, it, gdef, gates = resolved

    ordered = _order_gate(log, cfg, st, item, gate, recording=False)
    if isinstance(ordered, O.Outcome):
        return ordered

    if gdef.is_human_gate:
        return O.nothing(
            "gate.run",
            f"gate {gate!r} is a HUMAN-APPROVAL gate. It is not something you can run or "
            f"record — it is where the operator decides whether this work should proceed, "
            f"before the compute is spent.\n\n{gdef.prompt}\n\n"
            f"Show them what you are proposing, then ask them to run:\n"
            f"  ddflow approve {item} {gate}\n"
            f"  ddflow approve {item} {gate} --reject --reason '...'\n\n"
            f"There is deliberately no MCP tool for this.",
            id=item,
            gate=gate,
            outcome="",
        )
    if not gdef.is_command_gate:
        return O.nothing(
            "gate.run",
            f"gate {gate!r} is an AGENT gate — ddflow cannot perform it.\n\n{gdef.prompt}\n\n"
            f"When done: ddflow gate record {item} {gate} --outcome passed "
            f"--evidence '<what you ran / what it said>'",
            id=item,
            gate=gate,
            outcome="",
        )

    cwd = W.load_path(repo, it.worktree) if (gdef.cwd == "worktree" and it.worktree) else repo
    log.append("gate.started", item, {"gate": gate})
    keeper = _lease_keeper(log, cfg, it)
    result, ev = G.run_command_gate(
        gdef, cwd, on_tick=keeper, tick_s=max(1, cfg.lease.heartbeat_s) if keeper else 0
    )
    reason = ev.get("reason", "")
    if not reason and result != "passed":
        # Synthesised from what actually happened. The requirement that a non-pass carries
        # a reason exists so a human can act on it; for a command gate the exit code and
        # the output tail ARE that reason, and demanding the caller retype them would mean
        # the commonest outcome of all -- a failing suite -- could not be recorded at all.
        tail = ev.get("tail", "").strip()
        reason = f"`{gdef.command}` exited {ev.get('exit')}" + (
            f": {tail.splitlines()[-1][:160]}" if tail else ""
        )
    G.record(log, cfg, item, gate, result, reason=reason, evidence=ev, gates=gates)
    data: dict[str, Any] = {"gate": gate, "outcome": result, "evidence": ev, "id": item}
    exit_code = OUTCOME_EXIT[result]
    if exit_code == O.OK:
        return O.ok("gate.run", **data)
    if exit_code == O.FAIL:
        return O.failed("gate.run", reason or f"{gate} failed", **data)
    return O.nothing("gate.run", reason or f"{gate} could not report", **data)


@dataclass
class Evidence:
    """What the caller claims it did, as a record rather than five loose arguments.

    These five travel together everywhere — argparse flags, MCP input properties, the
    event payload — and grouping them is also what makes the distinction below legible:
    `note` is what a person wrote, while `tree_sha` and `diff_stat` are MEASURED here and
    deliberately not settable by the caller. A gate whose magnitude the caller supplies
    is a gate whose magnitude proves nothing.
    """

    note: str = ""
    command: str = ""
    exit_code: int | None = None
    model: str = ""
    output_file: str = ""


def record(
    repo: Path,
    item: str,
    gate: str,
    *,
    outcome: str = "",
    skip: bool = False,
    reason: str = "",
    evidence: Evidence | None = None,
    agent: str = "",
) -> O.Outcome:
    """Record an outcome an agent produced, or skip the gate on the record.

    One function for both because they differ in exactly two places — the outcome is
    forced to `skipped`, and a skip records no magnitude, since nothing was reviewed and
    a diff size would imply an inspection that did not happen.
    """
    from ..infra import worktree as W

    # `None`, not a shared default instance: a mutable default is one object for every
    # call, and `Evidence` is a dataclass somebody will add a list field to.
    evidence = evidence or Evidence()
    resolved = _resolve(repo, item, gate, agent)
    if isinstance(resolved, O.Outcome):
        return resolved
    log, cfg, st, it, gdef, gates = resolved

    ordered = _order_gate(log, cfg, st, item, gate, recording=True)
    if isinstance(ordered, O.Outcome):
        return ordered
    # The `warn` note. Carried rather than printed, so the surface decides where it goes
    # -- and so it is not silently lost, which is exactly what happened when this moved
    # out of `cmd_gate`: the api computed `ahead` and threw it away, and
    # `test_recording_a_gate_out_of_order_says_so` caught it on the full suite.
    warning = (
        f"NOTE: {_order_note(ordered, gate)} Recording anyway "
        f"([gates].enforce_order = '{cfg.gates.enforce_order}')."
        if ordered
        else ""
    )

    result = "skipped" if skip else outcome
    ev: dict[str, Any] = {}
    if evidence.note:
        ev["note"] = evidence.note
    if evidence.command:
        ev["command"] = evidence.command
    if evidence.exit_code is not None:
        ev["exit"] = evidence.exit_code
    if evidence.model:
        ev["model"] = evidence.model
    if evidence.output_file:
        try:
            txt = Path(evidence.output_file).read_text("utf-8", errors="replace")
        except OSError as exc:
            return O.failed("gate.record", f"--output-file unreadable: {exc}", id=item, gate=gate)
        ev["output_digest"] = G.digest(txt)
        ev["output_bytes"] = len(txt)
        ev["tail"] = txt[-2000:]

    if not skip:
        # WHICH tree and HOW MUCH, for AGENT gates too. These were added to
        # `run_command_gate` only, so they never reached the gates the rationale was
        # written about: "a review gate that passed over 4,000 changed lines in two
        # minutes is a different claim from one that passed over 12" -- and `rubber_duck`,
        # `critic` and `standards` are all agent gates recorded through this branch. The
        # feature missed its own motivating case.
        wt = (W.load_path(repo, it.worktree) if it.worktree else None) or repo
        ev.setdefault("tree_sha", G.tree_fingerprint(wt))
        ev.setdefault("diff_stat", G.diff_stat(wt))

    try:
        G.record(
            log,
            cfg,
            item,
            gate,
            result,
            reason=reason,
            evidence=ev or None,
            gates=gates,
            by=evidence.model,
        )
    except ValueError as exc:
        if gdef is not None and gdef.is_human_gate:
            return O.refused("gate.record", str(exc), id=item, gate=gate, outcome=result)
        return O.failed("gate.record", str(exc), id=item, gate=gate, outcome=result)
    return O.ok("gate.record", id=item, gate=gate, outcome=result, ahead=ordered, warning=warning)
