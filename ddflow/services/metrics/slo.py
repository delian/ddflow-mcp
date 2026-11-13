"""SLIs and SLOs for ddflow itself (R-observability 6): a table of service-level
indicators derived from the existing metrics fold, each held against a ``[slo]`` target
knob.

The SLIs read only the log the metrics service has already folded (``facts.collect``): no
new store, no scheduler, no network. Only queue age and CI false-red get a burn-rate view
(their window is long and their signal monotone); every other SLI rides the existing
N-of-W flow-control pattern (PM-detect) and reports a single value against its target. The
quality-gate failure rate (critic / rubber_duck raising a finding) is a review-health
SIGNAL, reported but never turned into a breach: a gate that fails 55-57 percent is doing
its job, not down.
"""

from __future__ import annotations

from ...core import progress as PR
from .providers import Context
from .stats import rate, stat
from .summary import gate_durations, outcome_table, queue_waits


def _breach(value: float | int | None, target: float | int | None) -> bool | None:
    """Whether ``value`` is over ``target``; None when the value could not be computed
    (below the sample floor, never zero)."""
    if value is None or target is None:
        return None
    return value > target


def _p90(values: list[float], minimum: int) -> float | None:
    return stat(values, minimum)["p90_s"]


def _review_signal(ctx: Context, minimum: int) -> float | None:
    """The share of critic/rubber_duck RAN verdicts that failed (a round that fails means
    it raised a finding). A signal of review quality, not an outage."""
    ran = [
        o
        for o in ctx.facts.outcomes
        if o.gate in PR.REVIEW_GATES and o.kind in ("passed", "failed", "partial", "unavailable")
    ]
    failed = [o for o in ran if o.kind == "failed"]
    return rate(len(failed), len(ran), minimum)


def _worst_unavailable(ctx: Context) -> float | None:
    """The highest per-gate unavailable rate in the window (ci ~11% < target); a skip is
    excluded from the rate and a gate under the floor is None, never zero."""
    rows = [r["unavailable_rate"] for r in outcome_table(ctx, "gate")]
    present = [r for r in rows if r is not None]
    return max(present) if present else None


def _live_holds(ctx: Context) -> int:
    """Live lanes still holding a lease at report time: an open lease is one the fold did
    not close (its end is the read time). A held item nobody finished is waste, not
    progress; the facts proxy for a stuck lane."""
    now = ctx.now
    return len(
        {lease.holder for lease in ctx.facts.leases if lease.end is None or lease.end >= now}
    )


def _events_per_item(ctx: Context, minimum: int) -> float | None:
    """Committed log growth per completed item: the mean number of gate verdicts, merges,
    findings and bugs the completed items added to the log. Growth that tracks item count
    is healthy; growth that tracks retries is rework."""
    spans = [s for s in ctx.facts.spans.values() if s.completed_at is not None]
    if len(spans) < max(minimum, 1):
        return None
    totals = []
    for s in spans:
        n = (
            sum(o.item == s.item for o in ctx.facts.outcomes)
            + len(ctx.facts.merges.get(s.item, ()))
            + sum(f.item == s.item for f in ctx.facts.findings)
            + sum(i == s.item for i, _t in ctx.facts.bugs)
        )
        totals.append(float(n))
    return sum(totals) / len(totals) if totals else None


def _red_main(ctx: Context, minimum: int) -> float | None:
    """CI false-red: the share of merges whose item drew a bug within
    ``[metrics] lock_red_main_window_s`` of the merge. Red CI is a capacity burn, so this
    is the second SLI with a burn-rate view."""
    lands = sorted(
        ((i, m) for i, ms in ctx.facts.landings.items() for m in ms if ctx.in_scope(i, m.at)),
        key=lambda p: p[1].at,
    )
    window = ctx.cfg.metrics.lock_red_main_window_s
    red = sum(
        any(i == b and m.at <= t <= m.at + window for b, t in ctx.facts.bugs) for i, m in lands
    )
    return rate(red, len(lands), minimum)


def _burn(
    value: float | None, target: float | None, window_s: float, exhaust_s: float
) -> dict | None:
    """The burn-rate view for queue age and CI false-red: how deep the SLI already is into
    its budget and how far it is from spending it. ``value`` is the observed level for this
    window and ``target`` the budget; the SLI is `burning` when the observed level spends
    the budget within ``exhaust_s`` (or already sits at/over it)."""
    if value is None or target is None or target <= 0 or value <= 0 or window_s <= 0:
        return None
    multiple = value / target
    time_to_exhaust = target / value * window_s
    return {
        "budget_multiple": round(multiple, 4),
        "time_to_exhaust_s": round(time_to_exhaust, 0),
        "burning": multiple >= 1.0 or time_to_exhaust < exhaust_s,
    }


def assess(ctx: Context) -> dict:
    """The SLI table: one row per SLI with its value, ``[slo]`` target, breach and, for the
    two burn-rate SLIs, the burn view. ``breached`` (or ``value``) is None under the sample
    floor or for a source that is not yet collected -- never a fabricated zero."""
    s = ctx.cfg.slo
    minimum = int(ctx.cfg.metrics.min_samples)

    gate = _p90([secs for _o, secs in gate_durations(ctx)], minimum)
    queue = _p90(queue_waits(ctx), minimum)
    unavailable = _worst_unavailable(ctx)
    red = _red_main(ctx, minimum)
    held = _live_holds(ctx)
    growth = _events_per_item(ctx, minimum)
    signal = _review_signal(ctx, minimum)

    rows = [
        {
            "name": "command_latency_p95",
            "value": None,
            "target": s.target_command_p95_s,
            "unit": "s",
            "breached": None,
            "burn": False,
            "note": "source: PM-sampler (per-command wall samples), not collected yet",
        },
        {
            "name": "gate_p90",
            "value": gate,
            "target": s.target_gate_p90_s,
            "unit": "s",
            "breached": _breach(gate, s.target_gate_p90_s),
            "burn": False,
            "note": "",
        },
        {
            "name": "queue_age",
            "value": queue,
            "target": s.target_queue_age_s,
            "unit": "s",
            "breached": _breach(queue, s.target_queue_age_s),
            "burn": True,
            "note": "",
        },
        {
            "name": "gate_unavailable_rate",
            "value": unavailable,
            "target": s.target_unavailable_rate,
            "unit": "",
            "breached": _breach(unavailable, s.target_unavailable_rate),
            "burn": False,
            "note": "",
        },
        {
            "name": "ci_false_red_rate",
            "value": red,
            "target": s.target_ci_false_red_rate,
            "unit": "",
            "breached": _breach(red, s.target_ci_false_red_rate),
            "burn": True,
            "note": "",
        },
        {
            "name": "stuck_lanes",
            "value": held,
            "target": s.target_stuck_lanes,
            "unit": "",
            "breached": _breach(held, s.target_stuck_lanes),
            "burn": False,
            "note": "live held lanes",
        },
        {
            "name": "log_events_per_item",
            "value": growth,
            "target": s.target_log_events_per_item,
            "unit": "",
            "breached": _breach(growth, s.target_log_events_per_item),
            "burn": False,
            "note": "committed events per completed item",
        },
        {
            "name": "review_finding_rate",
            "value": signal,
            "target": s.target_review_finding_rate,
            "unit": "",
            "breached": None,
            "burn": False,
            "note": "signal, not an outage",
        },
    ]
    burn = {
        r["name"]: _burn(r["value"], r["target"], s.burn_rate_window_s, s.burn_rate_exhaust_s)
        for r in rows
        if r["burn"]
    }
    breached = [r["name"] for r in rows if r["breached"] is True]
    return {"sli": rows, "burn_rate": burn, "breached": breached}


def ok(data: dict) -> bool:
    """A table's worth is that it discriminates: it is `ok` as soon as any SLI could be
    computed (a breach of none is a real green reading, not an empty fold)."""
    return any(r["value"] is not None for r in data["sli"])


def text(data: dict) -> str:
    rows = data["sli"]
    header = (
        "SLI                      value   target  breached  burn  note\n"
        "------------------------  ------  ------  --------  ----  ----"
    )
    lines = [header]
    for r in rows:
        value = "n/a" if r["value"] is None else f"{r['value']:g}"
        breached = "" if r["breached"] is None else ("BREACH" if r["breached"] else "ok")
        note = r["note"] or ("signal" if r["name"] == "review_finding_rate" else "")
        lines.append(
            f"{r['name']:<25} {value:>7} {r['target']:>7g}  {breached:>8}  "
            f"{'burn' if r['burn'] else '':>4}  {note}"
        )
    if data["breached"]:
        lines.append("over target: " + ", ".join(data["breached"]))
    return "\n".join(lines)


__all__ = ["assess", "ok", "text"]
