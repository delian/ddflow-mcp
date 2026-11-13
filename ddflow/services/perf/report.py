"""The perf report's data layer: one pure, deterministic structure every renderer reads.

For a window and the events the log already holds, this produces the sections that are
computable today -- per-gate time and failure rate, the cycle-time split, the lease and merge
wait, the rework loops and the drift findings -- each number carrying its ``n`` and an env
class, with ``None`` where there is not enough data. Sections whose SOURCE is not yet merged
(test times, resource use, LLM wait/tokens/cost, flaky rate, complexity) are named under
``not_collected``, never rendered as empty or zero (D-perf-metrics-answers: the per-agent and
per-model comparison is always present, and below the minimum it says not enough data).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field, replace

from ddflow.config import Config
from ddflow.services.metrics.providers import Context
from ddflow.services.metrics.stats import stat
from ddflow.services.metrics.summary import completed_spans, gate_durations

from ...core import gateyield

#: The report sections whose SOURCE module is not merged yet. Each names the task that ships
#: the data; a renderer renders "not collected" for it, never an empty table (r-unavailable).
NOT_COLLECTED = {
    "test_suite_size": "PM-test-durations",
    "slowest_files": "PM-test-durations",
    "resource_use": "PM-sampler",
    "llm_wait_tokens_cost": "PM-agent-calls",
    "flaky_rate": "PM-flaky",
    "complexity": "PM-complexity",
    "network": "not collected by decision (D-perf-metrics-answers .8)",
    "deltas": "PM-report-data (period-over-period deltas) - computed by the renderer over two windows",
    "findings": "PM-detect (merged) for drift findings; PM-auto for the proposals",
}


def _scoped_facts(ctx: Context):
    """The facts inside the window (outcomes/findings/bugs of in-scope items); the yield and
    rework sections read this so they describe the SAME window as the comparisons and waits."""
    f = ctx.facts
    return replace(
        f,
        outcomes=[o for o in f.outcomes if ctx.in_scope(o.item, o.at)],
        findings=[x for x in f.findings if ctx.in_scope(x.item, x.at)],
        bugs=[(i, t) for (i, t) in f.bugs if ctx.in_scope(i, t)],
        spans={item: s for item, s in f.spans.items() if ctx.in_scope(item, s.added_at)},
    )


def _per_gate(ctx: Context, minimum: int) -> list[dict]:
    per: dict[str, list[float]] = {}
    for o, secs in gate_durations(ctx):
        per.setdefault(o.gate, []).append(secs)
    yields = {r["gate"]: r for r in gateyield.gate_rows(_scoped_facts(ctx), minimum)}
    out = []
    for gate in sorted(
        set(per) | set(yields)
    ):  # a gate with a yield but no duration is not dropped
        y = yields.get(gate, {})
        out.append(
            {
                "gate": gate,
                **stat(per.get(gate, []), minimum),
                "items": y.get("items"),  # the yield row's own n, carried alongside the duration n
                "first_run_pass_rate": y.get("first_run_pass_rate"),
                "ever_failed_share": y.get("ever_failed_share"),
                "cost_per_confirmed_finding_s": y.get("cost_per_confirmed_finding_s"),
            }
        )
    return out


#: A verdict that RAN the gate. A SKIP did not run and an UNAVAILABLE could not run: both are
#: excluded from the rate (neither a pass nor a zero) and from ``n`` (D: unavailable never zero
#: or passed).
RAN_KINDS = frozenset({"passed", "failed", "partial"})


def _pass_rate(kinds: list[str], minimum: int) -> tuple[float | None, int]:
    """(passed / ran, ran count) -- skip and unavailable are excluded from both. Below the
    floor the rate is None (the renderer says not-enough-data), never zero."""
    ran = [k for k in kinds if k in RAN_KINDS]
    passed = ran.count("passed")
    return (passed / len(ran) if len(ran) >= max(minimum, 1) else None), len(ran)


def _agent_outcomes(ctx: Context, first: dict) -> dict[str, list[str]]:
    """per-agent: an item's claim agent, first-run "passed every RAN gate" (in-scope, re-claims
    count once, a skip-only item is omitted)."""
    out: dict[str, list[str]] = defaultdict(list)
    seen: set[tuple[str, str]] = set()
    for span in ctx.facts.spans.values():
        if span.added_at is None or not ctx.in_scope(span.item, span.added_at):
            continue
        kinds = [kind for (it, _g), kind in first.items() if it == span.item]
        ran = [k for k in kinds if k in RAN_KINDS]
        if not ran:
            continue
        clean = all(k == "passed" for k in ran)
        for _at, agent in span.claims:
            if (span.item, agent) in seen:
                continue
            seen.add((span.item, agent))
            out[agent].append("passed" if clean else "failed")
    return out


def _rows(names: dict[str, list[str]], minimum: int) -> list[dict]:
    out = []
    for name in sorted(names):
        rate, n = _pass_rate(names[name], minimum)
        out.append(
            {
                "name": name,
                "n": n,  # ran verdicts only; a skip or unavailable neither passes nor counts
                "env": "",  # log-derived tables are not env-keyed; the store's series carry env
                "first_run_pass_rate": rate,
            }
        )
    return out


def _comparisons(ctx: Context, minimum: int) -> dict[str, list[dict]]:
    """The per-MODEL, per-REVIEWER and per-AGENT comparison D-perf-metrics-answers always
    shows: the first-verdict pass rate over the RAN verdicts (a skip is excluded, so a retry
    and a skip do not inflate ``n`` or move the rate), and ``None`` below the sample floor."""
    first = gateyield._first_verdicts(ctx.facts)
    first_out: dict[tuple[str, str], object] = {}
    for o in sorted(
        ctx.facts.outcomes, key=lambda o: o.at
    ):  # time order, same basis as _first_verdicts
        key = (o.item, o.gate)
        if key not in first_out:
            first_out[key] = o
    groups: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for (item, gate), kind in first.items():
        o = first_out.get((item, gate))
        if o is None or not ctx.in_scope(item, o.at if hasattr(o, "at") else 0.0):
            continue  # outside the window: not part of this report
        groups["models"][o.model or "(unknown model)"].append(kind)
        groups["reviewers"][o.by or "(unknown reviewer)"].append(kind)
    groups["agents"] = _agent_outcomes(ctx, first)

    return {
        "models": _rows(groups["models"], minimum),
        "reviewers": _rows(groups["reviewers"], minimum),
        "agents": _rows(groups["agents"], minimum),
    }


def _cycle_split(ctx: Context, minimum: int) -> dict:
    # cycle time = completed - first claim; the split is the same for now (stages owns the
    # deeper split); this is the report's single number per item.
    spans = completed_spans(ctx)
    times = [s.cycle_s for s in spans if s.cycle_s is not None]
    return {**stat(times, minimum), "items": len(times)}


def _waits(ctx: Context, minimum: int) -> dict[str, dict]:
    """Lease (a refused claim) and merge (branch landed) wait, as p50/p90/max seconds."""
    lease_wait: list[float] = []
    for item, waits in ctx.facts.waits.items():
        span = ctx.facts.spans.get(item)
        if (
            span is not None
            and span.first_claim is not None
            and ctx.in_scope(item, span.first_claim)
        ):
            before = [w for w in waits if w <= span.first_claim]
            if before:
                lease_wait.append(span.first_claim - min(before))
    merge_wait: list[float] = []
    for item, merged in ctx.facts.merges.items():
        span = ctx.facts.spans.get(item)
        if (
            span is not None
            and span.completed_at is not None
            and span.completed_at <= merged[0]
            and ctx.in_scope(item, span.completed_at)
        ):
            merge_wait.append(merged[0] - span.completed_at)
    return {
        "lease": stat(lease_wait, minimum),
        "merge": stat(merge_wait, minimum),
    }


@dataclass
class Report:
    """The whole window's report data. ``sections`` are the computed tables/series; the
    ``not_collected`` sections are named with the task that will provide them."""

    gates: list[dict] = field(default_factory=list)
    cycle: dict = field(default_factory=dict)
    waits: dict = field(default_factory=dict)
    rework: list[dict] = field(default_factory=list)
    comparisons: dict = field(default_factory=dict)
    not_collected: dict = field(default_factory=dict)

    def find(self, section: str) -> list[dict] | dict:
        if section in ("models", "reviewers", "agents"):
            return self.comparisons.get(section, [])
        if hasattr(self, section):
            return getattr(self, section)
        # An uncomputed section is named as not-collected, never an empty table.
        return {"not_collected": self.not_collected.get(section, "")}


def build(ctx: Context, minimum: int | None = None) -> Report:
    """The report data as one structure. Deterministic for the same log and config."""
    cfg = Config()
    minimum = int(cfg.metrics.min_samples) if minimum is None else minimum
    return Report(
        gates=_per_gate(ctx, minimum),
        cycle=_cycle_split(ctx, minimum),
        waits=_waits(ctx, minimum),
        rework=gateyield.rework_loops(_scoped_facts(ctx)),
        comparisons=_comparisons(ctx, minimum),
        not_collected=dict(NOT_COLLECTED),
    )


__all__ = ["NOT_COLLECTED", "Report", "build"]
