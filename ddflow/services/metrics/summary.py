"""``ddflow metrics``: cycle time, gate time, review rounds, outcome rates, waits, rework.

Every figure is derived from the facts the log gives (``facts.collect``). A figure with
fewer than ``[metrics] min_samples`` samples, or one the log cannot support, is None and
named under ``unavailable``: never a zero.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable
from functools import lru_cache

from ...core import globs as G
from ...core import progress as PR
from .facts import Lease, Outcome, Span
from .providers import Context, dimension_names, key_for, run_providers
from .stats import rate, stat

VERDICTS = ("passed", "failed", "partial", "unavailable", "skipped")
#: Verdicts that ran the gate (a skip did not): the ones a duration belongs to.
RAN = ("passed", "failed", "partial", "unavailable")


def completed_spans(ctx: Context) -> list[Span]:
    """Spans finished inside the view that were claimed before they finished."""
    return [
        s
        for s in ctx.facts.spans.values()
        if s.cycle_s is not None and ctx.in_scope(s.item, s.completed_at)
    ]


def scoped_outcomes(ctx: Context) -> list[Outcome]:
    return [o for o in ctx.facts.outcomes if ctx.in_scope(o.item, o.at)]


def gate_durations(ctx: Context) -> list[tuple[Outcome, float]]:
    """(outcome, seconds from its gate starting to the verdict). The start is the latest
    ``gate.started`` since the item's previous verdict on that gate; failing that, a
    review's own recorded ``elapsed_s``. An outcome with neither has no duration."""
    out: list[tuple[Outcome, float]] = []
    last_verdict: dict[tuple[str, str], float] = {}
    for o in sorted(ctx.facts.outcomes, key=lambda o: o.at):
        key = (o.item, o.gate)
        floor = last_verdict.get(key, 0.0)
        last_verdict[key] = o.at
        if o.kind not in RAN or not ctx.in_scope(o.item, o.at):
            continue
        began = [t for t in ctx.facts.starts.get(key, ()) if floor <= t <= o.at]
        if began:
            out.append((o, o.at - max(began)))
        elif o.elapsed_s is not None:
            out.append((o, o.elapsed_s))
    return out


def review_rounds(ctx: Context) -> list[int]:
    """Review verdicts per completed item (0 for an item whose reviews were all skipped)."""
    counts: Counter[str] = Counter(
        o.item for o in scoped_outcomes(ctx) if o.gate in PR.REVIEW_GATES and o.kind in RAN
    )
    return [counts.get(s.item, 0) for s in completed_spans(ctx)]


def outcome_table(ctx: Context, group_by: str) -> list[dict]:
    """Verdict counts and failure, skip and unavailable rates per group of ``group_by``."""
    minimum = int(ctx.cfg.metrics.min_samples)
    groups: dict[str, Counter] = defaultdict(Counter)
    for o in scoped_outcomes(ctx):
        if key := key_for(group_by, o, ctx):  # a dimension that does not apply gives ''
            groups[key][o.kind] += 1
    rows = []
    for name, c in sorted(groups.items()):
        total = sum(c.values())
        rows.append(
            {
                "group": name,
                "n": total,
                **{v: c.get(v, 0) for v in VERDICTS},
                "failure_rate": rate(c["failed"], total, minimum),
                "skip_rate": rate(c["skipped"], total, minimum),
                "unavailable_rate": rate(c["unavailable"], total, minimum),
            }
        )
    return rows


def queue_waits(ctx: Context) -> list[float]:
    """Ready (every dependency done) to first claimed, per item. An item claimed before it
    was ready waited for nothing and has no sample; so does one whose readiness the log
    does not date."""
    waits = []
    for s in ctx.facts.spans.values():
        if s.ready_at is None or s.first_claim is None or not ctx.in_scope(s.item, s.first_claim):
            continue
        if s.first_claim >= s.ready_at:
            waits.append(s.first_claim - s.ready_at)
    return waits


@lru_cache(maxsize=65536)
def _clash(a: tuple[str, ...], b: tuple[str, ...]) -> bool:
    return any(G.overlap(x, y) for x in a for y in b)


def _union_s(spans: Iterable[tuple[float, float]]) -> float:
    total, edge = 0.0, float("-inf")
    for start, end in sorted(spans):
        start = max(start, edge)  # noqa: PLW2901
        if end > start:
            total += end - start
            edge = end
    return total


def lease_waits(ctx: Context) -> list[float]:
    """Of each item's queue wait, the seconds another holder's lease on overlapping globs
    was live: the part of the wait a glob conflict explains. An item with no recorded globs
    has no sample."""
    by_item: dict[str, Lease] = {}
    for lease in ctx.facts.leases:
        by_item.setdefault(lease.item, lease)
    out = []
    for s in ctx.facts.spans.values():
        mine = by_item.get(s.item)
        if s.ready_at is None or s.first_claim is None or mine is None or not mine.globs:
            continue
        if s.first_claim <= s.ready_at or not ctx.in_scope(s.item, s.first_claim):
            continue
        lo, hi = s.ready_at, s.first_claim
        clipped = []
        for ls in ctx.facts.leases:
            end = ls.end if ls.end is not None else ctx.now
            if ls.item == s.item or not ls.globs or ls.start >= hi or end <= lo:
                continue  # not live inside the wait
            if _clash(mine.globs, ls.globs):
                clipped.append((max(lo, ls.start), min(hi, end)))
        out.append(_union_s(clipped))
    return out


def rework(ctx: Context) -> tuple[int, int]:
    """(completed items in the view, those a bug was filed against after they completed)."""
    done = [
        s
        for s in ctx.facts.spans.values()
        if s.completed_at and ctx.in_scope(s.item, s.completed_at)
    ]
    hit = {
        s.item
        for s in done
        if any(item == s.item and at > (s.completed_at or 0) for item, at in ctx.facts.bugs)
    }
    return len(done), len(hit)


def build(ctx: Context, group_by: str = "gate") -> dict:
    """The whole ``metrics`` summary as plain data."""
    minimum = int(ctx.cfg.metrics.min_samples)
    if group_by not in dimension_names():
        raise ValueError(f"unknown group-by {group_by!r}: one of {', '.join(dimension_names())}")
    unavailable: dict[str, str] = {}

    def need(name: str, values: list, what: str, unit: str = "s") -> dict:
        s = stat(values, minimum, unit=unit)
        if s["n"] < max(minimum, 1):
            unavailable[name] = f"{s['n']} {what}, need {minimum}"
        return s

    cycle = need("cycle_time", [s.cycle_s for s in completed_spans(ctx)], "completed items")
    per_gate: dict[str, list[float]] = defaultdict(list)
    for o, secs in gate_durations(ctx):
        per_gate[o.gate].append(secs)
    gate_time = [{"gate": g, **stat(v, minimum)} for g, v in sorted(per_gate.items())]
    if not any(r["p50_s"] is not None for r in gate_time):
        unavailable["gate_time"] = f"no gate with {minimum} timed runs"
    rounds = [float(n) for n in review_rounds(ctx)]
    rounds_stat = need("review_rounds", rounds, "completed items", "rounds")
    done, hit = rework(ctx)
    rework_rate = rate(hit, done, minimum)
    if rework_rate is None:
        unavailable["rework"] = f"{done} completed items, need {minimum}"
    providers, failed = run_providers(ctx)
    for name, why in failed.items():
        unavailable[f"provider:{name}"] = why
    table = outcome_table(ctx, group_by)
    if not any(r["failure_rate"] is not None for r in table):
        unavailable["outcomes"] = f"no {group_by} with {minimum} verdicts"
    return {
        "group_by": group_by,
        "cycle_time": cycle,
        "gate_time": gate_time,
        "review_rounds": rounds_stat,
        "outcomes": table,
        "queue_wait": need("queue_wait", queue_waits(ctx), "items with a dated wait"),
        "lease_wait": need("lease_wait", lease_waits(ctx), "items with recorded globs"),
        "rework": {"completed": done, "reworked": hit, "rate": rework_rate},
        "providers": providers,
        "unavailable": unavailable,
    }


def has_figures(data: dict) -> bool:
    """Whether any figure at all could be computed (else the answer is exit 2)."""
    return (
        data["cycle_time"]["p50_s"] is not None
        or any(r["p50_s"] is not None for r in data["gate_time"])
        or data["review_rounds"]["p50_rounds"] is not None
        or data["queue_wait"]["p50_s"] is not None
        or data["lease_wait"]["p50_s"] is not None
        or data["rework"]["rate"] is not None
        or any(r["failure_rate"] is not None for r in data["outcomes"])
        or any(r.get("value") is not None for r in data["providers"])
    )
