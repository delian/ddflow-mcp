"""Runs to merge: gate re-runs and first-pass rate per merged item (D-runs-per-merge-r2).

Read-only and pure: folds the event log once with ``metrics.facts.collect`` (the collector
every ``ddflow metrics`` view uses; there is no second one) and, over the merged items in
the configurable window, answers:

* per merged item -- how many runs it took (each gate verdict of kind passed / failed /
  partial / unavailable is one run: a gate start-and-verdict, a review round, a gate
  re-run after a failure) and whether it passed first time (none of its gates failed, and
  none was unavailable or partial, before it completed -- the swarm ledger's first pass);
* rolled up per phase, per model and per gate kind, with deterministic gates labelled
  apart from the model (rubber_duck / critic) gates;
* the window ([metrics].runs_window_days, default 30) and the outlier flag
  ([metrics].outlier_runs).

A figure with fewer than ``[metrics].min_samples`` merged items is None, never a zero.
``provider`` registers the headline rows the summary shows under ``[runs]``; ``rollup`` is
the full per-item / per-phase / per-model / per-gate report.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

from ...config import Config
from ...core import progress as PR
from ...core.model import State
from ..swarm.ledger import _author_model, _lease_models, _models
from .facts import Facts, Outcome, Span, collect
from .providers import Context

#: Verdicts that ran the gate (a skip did not): one run each.
RAN = ("passed", "failed", "partial", "unavailable")
#: Outcomes that bar a first pass (a failed gate, or one that could not run honestly).
_NOT_PASS = ("unavailable", "partial")

#: How ``group_by`` may roll the per-item figures up (the grouping knob).
GROUP_BYS = ("phase", "model", "gate")

DAY_S = 86400.0


def _rate(num: int, den: int, minimum: int) -> float | None:
    return num / den if den >= max(minimum, 1) else None


def window_start(cfg: Config, now: float, since: float | None) -> float:
    """The window's floor: an explicit ``since`` else [metrics].runs_window_days back."""
    return since if since is not None else now - float(cfg.metrics.runs_window_days) * DAY_S


def merged_spans(facts: Facts, state: State, phase: str, floor: float) -> list[Span]:
    """Work items (not phases) first completed inside the window and phase."""
    out = []
    for s in facts.spans.values():
        if s.kind == "phase" or s.completed_at is None or s.completed_at < floor:
            continue
        if phase and state is not None and PR.phase_of(state, s.item) != phase:
            continue
        out.append(s)
    return out


def _per_item(
    facts: Facts, state: State, merged: list[Span], sessions: dict, leases: dict
) -> list[dict]:
    """The per-item rollup: each merged item's runs and first-pass."""
    by_item: dict[str, list[Outcome]] = defaultdict(list)
    for o in facts.outcomes:
        by_item[o.item].append(o)
    rows = []
    for s in merged:
        done = s.completed_at
        before = [o for o in by_item.get(s.item, ()) if o.at <= done]
        failed = sum(o.kind == "failed" for o in before)
        weak = sum(o.kind in _NOT_PASS for o in before)
        runs = sum(1 for o in before if o.kind in RAN)
        passes = sum(1 for o in before if o.kind == "passed")
        at, author = next(((t, a) for t, a in reversed(s.claims) if t <= done), (0.0, ""))
        model = _author_model(s.item, author, at, sessions, leases) if author else ""
        rows.append(
            {
                "item": s.item,
                "phase": PR.phase_of(state, s.item) if state else "",
                "model": model,
                "kind": s.kind,
                "runs": runs,
                "passes": passes,
                #: the share of its runs that passed: 1/1 = 100% for a first-pass item,
                #: 1/3 = 33% for an item that needed three runs (the acceptance's two rates).
                "first_pass_rate": passes / runs if runs else None,
                "first_pass": not failed and not weak,
                "outlier": False,  # filled by caller once the run count is known
            }
        )
    return rows


def _per_gate(facts: Facts, merged: list[Span], minimum: int) -> list[dict]:
    """Per gate kind: its items, runs and first-verdict pass rate, deterministically labelled.

    ``first_pass`` here is per (item, gate): the item's FIRST verdict on the gate was
    ``passed``, before completion. A deterministic gate (anything not rubber_duck/critic)
    is labelled ``gate_kind="deterministic"``, the model gates ``"model"``."""
    done = {s.item: s.completed_at for s in merged}
    first: dict[tuple[str, str], str] = {}
    runs: dict[str, int] = defaultdict(int)
    for o in facts.outcomes:
        at_done = done.get(o.item)
        if at_done is None or o.at > at_done:
            continue
        if o.kind in RAN:
            runs[o.gate] += 1
            # the FIRST RUN verdict: a skip before the first run is not one.
            first.setdefault((o.item, o.gate), o.kind)
    items: dict[str, int] = defaultdict(int)
    passed: dict[str, int] = defaultdict(int)
    for (_item, gate), kind in first.items():
        items[gate] += 1
        passed[gate] += kind == "passed"
    rows = []
    for gate in sorted(items):
        rows.append(
            {
                "gate": gate,
                "gate_kind": "model" if gate in PR.REVIEW_GATES else "deterministic",
                "items": items[gate],
                "first_pass": passed[gate],
                "first_pass_rate": _rate(passed[gate], items[gate], minimum),
                "runs": runs[gate],
            }
        )
    return rows


def _rollups(rows: list[dict], attribute: str, minimum: int) -> list[dict]:
    """Group the per-item rows by one per-item attribute into first-pass rate and runs."""
    by: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by[r[attribute] or "(none)"].append(r)
    out = []
    for name in sorted(by):
        group = by[name]
        passes = sum(r["first_pass"] for r in group)
        out.append(
            {
                "group": name,
                "items": len(group),
                "first_pass": passes,
                "first_pass_rate": _rate(passes, len(group), minimum),
                "runs": sum(r["runs"] for r in group),
            }
        )
    return out


def rollup(
    events: Sequence,
    state: State,
    cfg: Config,
    now: float,
    *,
    phase: str = "",
    since: float | None = None,
    group_by: str = "gate",
) -> dict:
    """The whole report as plain data: per item, per group and per gate kind.

    ``group_by`` is one of ``phase``|``model``|``gate`` (the grouping knob); changing it
    changes ``rollups`` and nothing else, so a before/after reads the same merged set two ways."""
    if group_by not in GROUP_BYS:
        raise ValueError(f"unknown group-by {group_by!r}: one of {', '.join(GROUP_BYS)}")
    facts = collect(events, state, now, cfg)
    floor = window_start(cfg, now, since)
    merged = merged_spans(facts, state, phase, floor)
    minimum = max(int(cfg.metrics.min_samples), 1)
    sessions = _models(events)
    leases = _lease_models(events)
    rows = _per_item(facts, state, merged, sessions, leases)
    outliers = max(int(cfg.metrics.outlier_runs), 0)
    for r in rows:
        r["outlier"] = outliers > 0 and r["runs"] >= outliers
    first_pass_items = sum(r["first_pass"] for r in rows)
    n = len(rows)
    per_phase = _rollups(rows, "phase", minimum)
    per_model = _rollups(rows, "model", minimum)
    per_gate = _per_gate(facts, merged, minimum)
    #: The grouping knob: `group_by` selects WHICH rollup is the report's summary; the
    #: other two stay present so one read serves every grouping (a before/after reads the
    #: same merged set several ways without a second fold).
    rollups = {"phase": per_phase, "model": per_model, "gate": per_gate}[group_by]
    return {
        "group_by": group_by,
        "window": {
            "days": cfg.metrics.runs_window_days,
            "since": floor,
        },
        "merged": n,
        "runs": sum(r["runs"] for r in rows),
        "first_pass": first_pass_items,
        "first_pass_rate": _rate(first_pass_items, n, minimum),
        "outliers": sorted(r["item"] for r in rows if r["outlier"]),
        "per_item": rows,
        "per_phase": per_phase,
        "per_model": per_model,
        "per_gate": per_gate,
        "rollups": rollups,
        "unavailable": (
            {}
            if n >= minimum
            else {"first_pass_rate": f"{n} merged items, need {minimum}"}
        ),
    }


def provider(ctx: Context) -> list[dict]:
    """The headline rows ``ddflow metrics`` (and MCP, and brief) show under ``[runs]``.

    Computed from the one fold ``Context.facts`` without re-folding; only the aggregate
    figures the summary needs, never a per-item row (that is ``rollup``)."""
    cfg = ctx.cfg
    floor = ctx.since if ctx.since is not None else ctx.now - float(cfg.metrics.runs_window_days) * DAY_S
    merged = merged_spans(ctx.facts, ctx.facts.state, ctx.phase, floor)
    by_item: dict[str, list[Outcome]] = defaultdict(list)
    for o in ctx.facts.outcomes:
        by_item[o.item].append(o)
    done = {s.item: s.completed_at for s in merged}
    runs_total = 0
    first_pass_items = 0
    n = len(merged)
    minimum = max(int(cfg.metrics.min_samples), 1)
    for it, at_done in done.items():
        before = [o for o in by_item.get(it, ()) if o.at <= at_done]
        failed = sum(o.kind == "failed" for o in before)
        weak = sum(o.kind in _NOT_PASS for o in before)
        runs_total += sum(1 for o in before if o.kind in RAN)
        first_pass_items += not failed and not weak
    return [
        {
            "metric": "runs.first_pass_rate",
            "group": "",
            "n": n,
            "value": _rate(first_pass_items, n, minimum),
        },
        {
            "metric": "runs.runs_per_merge",
            "group": "",
            "n": n,
            "value": runs_total / n if n >= minimum else None,
        },
        {"metric": "runs.total_runs", "group": "", "n": n, "value": runs_total if n >= minimum else None},
    ]
