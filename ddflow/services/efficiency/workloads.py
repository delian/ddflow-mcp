"""Reference workloads: fixed, deterministic tasks whose pass is judged by ddflow's own
records, never by an agent's words.

The fixed set lives as DATA in ``ddflow/templates/efficiency/workloads.toml`` (the
record contract). A workload is a small loop over a throwaway project -- claim and
finish a task, run a gate, record a bug with its regression test, resolve a refusal --
and a ``pass_rule`` that reads ddflow's event log (``item.started``, ``item.completed``,
``gate.passed``, ``gate.failed``, ``bug.found``, ``bug.fixed``, ...). The pass/fail
verdict is built ONLY from those records; a claim of success without the named records
scores FAIL, never PASS.

A scripted fake lane runs the whole set deterministically at no model spend (for CI). A
live run against a real agent is gated behind a budget and recorded consent so it can
never spend silently.
"""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

PASS = "PASS"
FAIL = "FAIL"
UNRUN = "UNRUN"

#: The gate outcomes ddflow records (ddflow/core/records/gates.py ``GateOutcome``). A
#: gate that was never run records nothing of these -- silence, and silence is not a pass.
GATE_OUTCOMES: tuple[str, ...] = (
    "passed",
    "failed",
    "skipped",
    "unavailable",
    "partial",
)
GATE_OUTCOME_KINDS: frozenset[str] = frozenset(f"gate.{o}" for o in GATE_OUTCOMES)

#: Pass-rule kinds this module evaluates. The data file names one per workload.
_PASS_RULE_KINDS: frozenset[str] = frozenset(
    {
        "item-completed",
        "gate-outcome-recorded",
        "bug-with-regression-test",
        "refusal-resolved",
    }
)

_TEMPLATE = (
    Path(__file__).resolve().parents[3] / "ddflow" / "templates" / "efficiency" / "workloads.toml"
)


class LiveRunRefused(RuntimeError):
    """A live run was asked for without a budget and recorded consent."""


@dataclass(frozen=True)
class PassRule:
    """The declared pass rule: a kind and its parameters, read from the data file."""

    kind: str
    item: str = ""
    gates: tuple[str, ...] = ()
    gate: str = ""
    outcome: str = ""
    bug: str = ""


@dataclass(frozen=True)
class Workload:
    """One fixed workload, as declared in the data file."""

    id: str
    name: str
    summary: str = ""
    lane: str = "fake"
    spend: str = "none"
    steps: tuple[str, ...] = ()
    pass_rule: PassRule = PassRule(kind="")


@dataclass(frozen=True)
class Record:
    """One ddflow record (an event): kind, subject and payload, as read from the log."""

    kind: str
    subject: str = ""
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Report:
    """The verdict for one workload run, with the records that decided it."""

    workflow_id: str
    outcome: str
    reason: str = ""
    matched: tuple[Record, ...] = ()
    missing: tuple[str, ...] = ()


def load_workloads(source: str | Path | None = None) -> dict[str, Workload]:
    """Read the fixed workloads and their pass rules from the data file.

    The file is the record contract: ids, names, steps and ``pass_rule`` tables. A
    ``pass_rule.kind`` this module does not evaluate is refused, so a workload can
    never silently evaluate to ``UNRUN``-by-mistake.
    """
    path = Path(source) if source else _TEMPLATE
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    workloads: dict[str, Workload] = {}
    for block in raw.get("workload", []):
        rule_raw = block.get("pass_rule", {})
        kind = str(rule_raw.get("kind", ""))
        if kind not in _PASS_RULE_KINDS:
            raise ValueError(
                f"workload {block.get('id')!r} has unknown pass_rule.kind {kind!r}"
            )
        rule = PassRule(
            kind=kind,
            item=str(rule_raw.get("item", "")),
            gates=tuple(g or "" for g in rule_raw.get("gates", ())),
            gate=str(rule_raw.get("gate", "")),
            outcome=str(rule_raw.get("outcome", "")),
            bug=str(rule_raw.get("bug", "")),
        )
        steps = tuple(str(s) for s in block.get("steps", ()))
        workloads[str(block["id"])] = Workload(
            id=str(block["id"]),
            name=str(block.get("name", block["id"])),
            summary=str(block.get("summary", "")),
            lane=str(block.get("lane", "fake")),
            spend=str(block.get("spend", "none")),
            steps=steps,
            pass_rule=rule,
        )
    return workloads


def records_from_log(root: str | Path) -> tuple[Record, ...]:
    """ddflow's own records for a project: every event in its ``.ddflow/events`` shards."""
    events_dir = Path(root) / ".ddflow" / "events"
    if not events_dir.is_dir():
        return ()
    out: list[Record] = []
    for shard in sorted(events_dir.glob("*.jsonl")):
        for line in shard.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            data = event.get("data")
            out.append(
                Record(
                    kind=str(event.get("kind", "")),
                    subject=str(event.get("subject", "")),
                    data=data if isinstance(data, dict) else {},
                )
            )
    return tuple(out)


def _event(records: Iterable[Record], kind: str) -> list[Record]:
    return [r for r in records if r.kind == kind]


def _rule_item_completed(rule: PassRule, rs: list[Record]) -> tuple[bool, list[str]]:
    started = _event(rs, "item.started")
    completed = _event(rs, "item.completed")
    matched = [r for r in started + completed if r.subject == rule.item]
    missing: list[str] = []
    if not any(r.subject == rule.item for r in started):
        missing.append(f"item.started:{rule.item}")
    if not any(r.subject == rule.item for r in completed):
        missing.append(f"item.completed:{rule.item}")
    for gate in rule.gates:
        outcomes = [
            r
            for r in rs
            if r.kind in GATE_OUTCOME_KINDS
            and r.data.get("gate") == gate
            and r.subject == rule.item
        ]
        if not outcomes:
            missing.append(f"gate outcome:{gate}")
        matched.extend(outcomes)
    return not missing, matched, missing


def _rule_gate_outcome(rule: PassRule, rs: list[Record]) -> tuple[bool, list[str]]:
    kind = f"gate.{rule.outcome}" if rule.outcome else ""
    hits = [r for r in rs if r.kind == kind and r.data.get("gate") == rule.gate]
    missing = [] if hits else [f"{kind}:{rule.gate}"]
    return not missing, hits, missing


def _rule_bug_with_test(rule: PassRule, rs: list[Record]) -> tuple[bool, list[str]]:
    found = [r for r in _event(rs, "bug.found") if r.subject == rule.bug]
    fixed = [r for r in _event(rs, "bug.fixed") if r.subject == rule.bug]
    with_test = [r for r in fixed if r.data.get("regression_test") or r.data.get("regression_tests")]
    missing: list[str] = []
    if not found:
        missing.append(f"bug.found:{rule.bug}")
    if not with_test:
        missing.append(f"bug.fixed+regression-test:{rule.bug}")
    return not missing, found + with_test, missing


def _rule_refusal_resolved(rule: PassRule, rs: list[Record]) -> tuple[bool, list[str]]:
    refused = [r for r in _event(rs, "gate.failed") if r.data.get("gate") == rule.gate]
    passed = [r for r in _event(rs, "gate.passed") if r.data.get("gate") == rule.gate]
    missing: list[str] = []
    if not refused:
        missing.append(f"refusal (gate.failed):{rule.gate}")
    if not passed:
        missing.append(f"resolution (gate.passed):{rule.gate}")
    return not missing, refused + passed, missing


def _evaluate_rule(rule: PassRule, records: Iterable[Record]) -> tuple[bool, list[Record], list[str]]:
    """Consult ddflow's records only; there is no agent-words input (the falsifier)."""
    return _RULE_APPLY.get(rule.kind, _rule_unknown)(rule, list(records))


def _rule_unknown(rule: PassRule, rs: list[Record]) -> tuple[bool, list[Record], list[str]]:
    # Unreachable: load_workloads refuses unknown kinds.
    return False, [], [f"unknown pass kind {rule.kind!r}"]


_RULE_APPLY = {
    "item-completed": _rule_item_completed,
    "gate-outcome-recorded": _rule_gate_outcome,
    "bug-with-regression-test": _rule_bug_with_test,
    "refusal-resolved": _rule_refusal_resolved,
}


def evaluate(workload: Workload, records: Iterable[Record]) -> Report:
    """Score one workload run. The verdict reads ddflow's records only."""
    ok, matched, missing = _evaluate_rule(workload.pass_rule, records)
    outcome = PASS if ok else FAIL
    reason = (
        f"{workload.name}: all required records present"
        if ok
        else f"{workload.name}: missing " + ", ".join(missing)
    )
    return Report(workflow_id=workload.id, outcome=outcome, reason=reason,
                  matched=tuple(matched), missing=tuple(missing))


def _scripted_records(workload: Workload) -> tuple[Record, ...]:
    """The records a scripted fake lane writes for one workload, deterministically.

    No model, no network, no subprocess: these are fixed records, so the lane is cheap
    enough for CI and identical on every run. Each step maps to the recoverable ddflow
    record the step would leave behind if done for real.
    """
    rule = workload.pass_rule
    if workload.id == "claim-and-finish-a-task":
        return (
            Record("item.started", rule.item, {"kind": "task"}),
            Record("gate.started", rule.item, {"gate": "implement"}),
            Record("gate.passed", rule.item, {"gate": "implement"}),
            Record("gate.passed", rule.item, {"gate": "unit_tests"}),
            Record("worktree.merged", rule.item, {"sha": "fake"}),
            Record("item.completed", rule.item, {"kind": "task"}),
        )
    if workload.id == "run-a-gate":
        return (Record(f"gate.{rule.outcome}", "demo.task", {"gate": rule.gate}),)
    if workload.id == "record-a-bug-with-regression-test":
        return (
            Record("bug.found", rule.bug, {"summary": "demo", "fix_task": "demo.fix"}),
            Record("bug.fixed", rule.bug, {"regression_test": "test_the_bug_is_guarded"}),
        )
    if workload.id == "resolve-a-refusal":
        return (
            Record("gate.failed", "demo.task", {"gate": rule.gate, "reason": "refused"}),
            Record("gate.passed", "demo.task", {"gate": rule.gate}),
            Record("item.completed", "demo.task", {"kind": "task"}),
        )
    return ()


def run_fake_lane(workload: Workload) -> Report:
    """Run one workload on the scripted fake lane and score it. Deterministic, no spend."""
    return evaluate(workload, _scripted_records(workload))


def run_all(workloads: dict[str, Workload] | None = None) -> dict[str, Report]:
    """Run every workload on the scripted fake lane (the CI path, at no spend)."""
    workloads = workloads if workloads is not None else load_workloads()
    return {wid: run_fake_lane(wl) for wid, wl in workloads.items()}


def run_live(
    workload: Workload,
    project: str | Path,
    *,
    budget: Any,
    consent: str = "",
    runner: Callable[[Workload, Path], Iterable[Record]] | None = None,
) -> Report:
    """Run one workload against a real agent in a throwaway project.

    Refused unless a budget is set AND consent is recorded: a live run spends, so it must
    never happen silently (r-consent-outward, r-no-silent-cost).

    With ``runner``, the return scores ONLY the records that runner just produced, so a
    project whose log already held the pass events is never scored as *this* run's
    success. Without ``runner`` no step ran, and the run is UNRUN -- never a FAIL that
    pretends the workload was attempted, and never a PASS over a stale log.
    """
    if not budget or not consent:
        raise LiveRunRefused(
            f"live run of {workload.id!r} needs a budget and recorded consent; "
            "give both, or run the fake lane"
        )
    if runner is None:
        return Report(
            workflow_id=workload.id,
            outcome=UNRUN,
            reason=f"{workload.name}: live run produced no records (no runner); nothing scored",
        )
    return evaluate(workload, runner(workload, Path(project)))
