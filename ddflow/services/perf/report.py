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
}


def _per_gate(ctx: Context, minimum: int) -> list[dict]:
    per: dict[str, list[float]] = {}
    for o, secs in gate_durations(ctx):
        per.setdefault(o.gate, []).append(secs)
    yields = {r["gate"]: r for r in gateyield.gate_rows(ctx.facts, minimum)}
    out = []
    for gate in sorted(per):
        y = yields.get(gate, {})
        out.append(
            {
                "gate": gate,
                **stat(per[gate], minimum),
                "first_run_pass_rate": y.get("first_run_pass_rate"),
                "ever_failed_share": y.get("ever_failed_share"),
                "cost_per_confirmed_finding_s": y.get("cost_per_confirmed_finding_s"),
            }
        )
    return out


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
    not_collected: dict = field(default_factory=dict)

    def find(self, section: str) -> list[dict] | dict:
        return getattr(self, section, [])


def build(ctx: Context, minimum: int | None = None) -> Report:
    """The report data as one structure. Deterministic for the same log and config."""
    cfg = Config()
    minimum = minimum or int(cfg.metrics.min_samples)
    return Report(
        gates=_per_gate(ctx, minimum),
        cycle=_cycle_split(ctx, minimum),
        waits=_waits(ctx, minimum),
        rework=gateyield.rework_loops(ctx.facts),
        not_collected=dict(NOT_COLLECTED),
    )


__all__ = ["NOT_COLLECTED", "Report", "build"]
