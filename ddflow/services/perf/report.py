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

from dataclasses import dataclass, field

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


def _per_gate(ctx: Context, minimum: int) -> list[dict]:
    per: dict[str, list[float]] = {}
    for o, secs in gate_durations(ctx):
        per.setdefault(o.gate, []).append(secs)
    yields = {r["gate"]: r for r in gateyield.gate_rows(ctx.facts, minimum)}
    out = []
    for gate in sorted(set(per) | set(yields)):  # a gate with a yield but no duration is not dropped
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


def _comparisons(ctx: Context, minimum: int) -> dict[str, list[dict]]:
    """The per-MODEL, per-REVIEWER and per-AGENT comparison D-perf-metrics-answers always
    shows: the first-verdict pass rate (retries do not inflate ``n``), and ``None`` below the
    sample floor (the renderer says not-enough-data) -- never an empty table."""
    from collections import defaultdict

    first = gateyield._first_verdicts(ctx.facts)
    first_out: dict[tuple[str, str], object] = {}
    for o in ctx.facts.outcomes:
        key = (o.item, o.gate)
        if key not in first_out:
            first_out[key] = o
    groups: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for (item, gate), kind in first.items():
        o = first_out.get((item, gate))
        if o is None:
            continue
        groups["models"][o.model or "(unknown model)"].append(kind)
        groups["reviewers"][o.by or "(unknown reviewer)"].append(kind)
    # per-agent: an item's claim agent, its first-run "passed every gate" (only IN-SCOPE items
    # with a recorded verdict; re-claims by the same agent count once, never inflate n).
    agent_outcomes: dict[str, list[str]] = defaultdict(list)
    seen_claims: set[tuple[str, str]] = set()
    for span in ctx.facts.spans.values():
        if not ctx.in_scope(span.item, span.added_at):
            continue
        verdicts = [(it, g) for (it, g) in first if it == span.item]
        if not verdicts:
            continue  # claimed but never gated: not a pass, not a failure -- omitted
        clean = all(first[(it, g)] == "passed" for (it, g) in verdicts)
        for _at, agent in span.claims:
            if (span.item, agent) in seen_claims:
                continue
            seen_claims.add((span.item, agent))
            agent_outcomes[agent].append("passed" if clean else "failed")
    groups["agents"] = agent_outcomes

    def rows(names: dict[str, list[str]]) -> list[dict]:
        out = []
        for name in sorted(names):
            kinds = names[name]
            passed = sum(1 for k in kinds if k == "passed")
            out.append(
                {
                    "name": name,
                    "n": len(kinds),
                    "env": "",  # log-derived tables are not env-keyed; the store's series carry env
                    "first_run_pass_rate": (
                        passed / len(kinds) if kinds and len(kinds) >= max(minimum, 1) else None
                    ),
                }
            )
        return out

    return {
        "models": rows(groups["models"]),
        "reviewers": rows(groups["reviewers"]),
        "agents": rows(groups["agents"]),
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
        if span is not None and span.first_claim is not None:
            before = [w for w in waits if w <= span.first_claim]
            if before:
                lease_wait.append(span.first_claim - min(before))
    merge_wait: list[float] = []
    for item, merged in ctx.facts.merges.items():
        span = ctx.facts.spans.get(item)
        if span is not None and span.completed_at is not None and span.completed_at <= merged[0]:
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
        return getattr(self, section, [])


def build(ctx: Context, minimum: int | None = None) -> Report:
    """The report data as one structure. Deterministic for the same log and config."""
    cfg = Config()
    minimum = int(cfg.metrics.min_samples) if minimum is None else minimum
    return Report(
        gates=_per_gate(ctx, minimum),
        cycle=_cycle_split(ctx, minimum),
        waits=_waits(ctx, minimum),
        rework=gateyield.rework_loops(ctx.facts),
        comparisons=_comparisons(ctx, minimum),
        not_collected=dict(NOT_COLLECTED),
    )


__all__ = ["NOT_COLLECTED", "Report", "build"]
