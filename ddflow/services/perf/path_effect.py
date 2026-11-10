"""The perf report's before/after effect block for workflow-path changes (WP-path-effect).

A pure, stdlib fold over the same facts every metrics view reads (``facts.collect``): this
module computes the SIX numbers R-path-merge keyed, holds their baselines (the R-path-merge
measurements, keyed by the PM env fingerprint), and states the effect -- current vs
baseline -- as one data block and one text block, ready for the PM-report-* renderer to
show under the other sections. No second collector: it reuses ``metrics.paths``'s
burst-corrected per-item process union and ``metrics.summary``'s completed spans.

The six (all lower-is-better): gate hours per merged item, claim-to-merge p50 and p90,
process share, sequential review-pair share, superseded-pass hours, and parked share.
Baselines are the R-path-merge numbers; a figure the log cannot support, or one from fewer
than ``[metrics] min_samples`` samples, is ``None`` carrying its ``n`` (never a zero).

Research: R-path-merge. Next call names the perf report that renders this block.
"""

from __future__ import annotations

from collections import defaultdict

from ...core import clock
from ..metrics import paths as _paths
from ..metrics.providers import Context
from ..metrics.stats import percentile
from ..metrics.summary import completed_spans

#: The one place a duration in hours is named (clock.DURATION_UNITS has no fraction unit).
HOUR = 3600.0

#: The process gates whose verdicts can run concurrently and so form a review PAIR.
REVIEW_GATES = ("rubber_duck", "critic")

#: How long an item may sit with no gate activity before it counts as PARKED
#: (R-path-merge measured "22 percent parked more than 2 h").
PARKED_THRESHOLD_S = 2 * HOUR

#: (metric key, human label, lower-is-better) -- the SIX numbers R-path-merge keyed, in
#: report order. one tuple drives :func:`effect` and :func:`text` so they cannot drift.
METRICS = (
    ("gate_hours_per_merged_s", "gate hours per merged item", True),
    ("claim_to_merge_p50_s", "claim-to-merge p50", True),
    ("claim_to_merge_p90_s", "claim-to-merge p90", True),
    ("process_share", "process share", True),
    ("sequential_review_pair_share", "sequential review-pair share", True),
    ("superseded_pass_s", "superseded-pass hours", True),
    ("parked_share", "parked share", True),
)

#: The R-path-merge baselines, keyed by the PM env fingerprint (``env.fingerprint().key``).
#: A key not present below falls back to "" -- the canonical numbers measured on THIS
#: machine class over 2026-10-03..11-02 (R-path-merge, 1,322 claimed+completed items).
#: A different machine class holds its OWN measured baseline under its key. Project-level
#: overrides land in the ``[perf_metrics]`` section with the perf config task; this table
#: is the default (r-everything-configurable keeps the knob, this keeps the default).
BASELINES: dict[str, dict[str, float]] = {
    "": {
        # 992 h of burst-corrected process over 1,322 items (R-path-merge probe output).
        "gate_hours_per_merged_s": 992 * HOUR / 1322,
        "claim_to_merge_p50_s": 1.33 * HOUR,  # cycle p50 1.33 h
        "claim_to_merge_p90_s": 9.7 * HOUR,  # cycle p90 9.7 h
        "process_share": 992 / 3702,  # 992 h process union of 3,702 h claim-to-merge
        "sequential_review_pair_share": 1378 / 2499,  # 1,378 of 2,499 review pairs
        "superseded_pass_s": 119 * HOUR,  # superseded passes across the whole window
        "parked_share": 0.22,  # 22 percent parked more than 2 h
    },
}


def baseline(env_key: str | None) -> dict[str, float]:
    """The R-path-merge baseline for one env fingerprint class, or the canonical one."""
    key = env_key or ""
    return dict(BASELINES.get(key, BASELINES[""]))


def _figure(value: float | None, n: int, minimum: int, what: str = "samples") -> dict:
    """One figure as ``{value, n}``; ``value`` withheld (None) below the sample floor, the
    ``n`` always shown and the shortfall named in ``unavailable`` (never a zero)."""
    if n >= max(minimum, 1):
        return {"value": value, "n": n}
    return {"value": None, "n": n, "unavailable": f"{n} {what}, need {minimum}"}


def _interval(o) -> tuple[float, float] | None:
    """A verdict's process window ``[at - elapsed, at]``; None when it recorded no time."""
    return (o.at - o.elapsed_s, o.at) if o.elapsed_s is not None else None


def superseded_pass_hours(ctx: Context) -> dict:
    """The summed wall time of PASSED verdicts a later outcome on the same item left behind
    (a different ``tree_sha`` recorded after it) -- the pass counted for a tree that is no
    longer current. Mirrors ``path_variants.superseded_passes`` but sums ``elapsed_s`` so
    the number is HOURS, not a count. A pass with no recorded time adds a count but no
    seconds; either way the value is the real sum, never floored (a window with no
    superseded pass is a true zero, not a missing measurement)."""
    by_item: dict[str, list] = defaultdict(list)
    for o in ctx.facts.outcomes:
        by_item[o.item].append(o)
    seconds = 0.0
    count = 0
    for rows in by_item.values():
        rows = sorted(rows, key=lambda o: o.at)
        for i, o in enumerate(rows):
            if o.kind != "passed" or not o.tree_sha or o.elapsed_s is None:
                continue
            if any(r.tree_sha and r.tree_sha != o.tree_sha for r in rows[i + 1 :]):
                seconds += o.elapsed_s
                count += 1
    if not completed_spans(ctx):
        return {"value": None, "n": 0}  # no completed item: no data, never a zero
    return {"value": seconds, "n": count}


def sequential_review_share(ctx: Context, minimum: int) -> dict:
    """The share of rubber_duck/critic review PAIRS that ran SEQUENTIALLY: neither combined
    into one command nor overlapping. A pair is the latest rubber_duck and the latest
    critic verdict on one tree; ``combined`` when either verdict's ``combined_gates`` names
    the other (D-path-merge-answers), ``concurrent`` when both timed verdicts' process
    windows overlap, ``sequential`` otherwise (an untimed single-run verdict is presumed
    non-overlapping). Below the pair floor the share is None, the breakdown is not."""
    seq = conc = combined = 0
    by_tree: dict[str, list] = defaultdict(list)
    for s in completed_spans(ctx):
        for o in ctx.facts.outcomes:
            if o.item == s.item and o.gate in REVIEW_GATES and o.kind in (
                "passed",
                "failed",
                "partial",
            ):
                by_tree[(s.item, o.tree_sha)].append(o)
    for rows in by_tree.values():
        rd = [o for o in rows if o.gate == "rubber_duck"]
        cr = [o for o in rows if o.gate == "critic"]
        if not rd or not cr:
            continue
        r = max(rd, key=lambda o: o.at)
        c = max(cr, key=lambda o: o.at)
        if "rubber_duck" in c.combined_gates or "critic" in r.combined_gates:
            combined += 1
            continue
        ri, ci = _interval(r), _interval(c)
        if ri is not None and ci is not None and ri[0] < ci[1] and ci[0] < ri[1]:
            conc += 1
        else:
            seq += 1
    total = seq + conc + combined
    value = seq / total if total else None
    out = {"n": total, "sequential": seq, "concurrent": conc, "combined": combined}
    return {
        **out,
        "value": value if total >= max(minimum, 1) else None,
        "unavailable": None if total >= max(minimum, 1) else f"{total} pairs, need {minimum}",
    }


def parked_share(ctx: Context, minimum: int) -> dict:
    """The share of completed items that were PARKED: a continuous idle stretch longer than
    ``PARKED_THRESHOLD_S`` (no gate outcome, no claim) anywhere in their first-claim to
    completion life. R-path-merge measured 22 percent parked more than 2 h."""
    parked = 0
    n = 0
    outs_by_item: dict[str, list] = defaultdict(list)
    for o in ctx.facts.outcomes:
        outs_by_item[o.item].append(o.at)
    for s in completed_spans(ctx):
        if s.first_claim is None or s.completed_at is None:
            continue
        n += 1
        marks = {s.first_claim, s.completed_at}
        marks.update(t for t, _a in s.claims if s.first_claim <= t <= s.completed_at)
        marks.update(t for t in outs_by_item[s.item] if s.first_claim <= t <= s.completed_at)
        ordered = sorted(marks)
        if any(b - a > PARKED_THRESHOLD_S for a, b in zip(ordered, ordered[1:])):
            parked += 1
    value = parked / n if n else None
    return _figure(value, n, minimum, what="completed items") | {"parked": parked}


def current(ctx: Context) -> dict[str, dict]:
    """The six current numbers over the window. Rates and percentiles carry the sample
    floor; the superseded hour-sum and the seq/concurrent/combined breakdown do not."""
    minimum = int(ctx.cfg.metrics.min_samples)
    spans = completed_spans(ctx)
    n = len(spans)
    paths = _paths.build(ctx)["paths"]
    union = sum(p["process_union_s"] for p in paths)
    cycles = [s.cycle_s for s in spans if s.cycle_s is not None]
    cycle = sum(cycles)
    gate_hours = union / n if n else None
    process_share = union / cycle if cycle else None
    return {
        "gate_hours_per_merged_s": _figure(gate_hours, n, minimum, "completed items"),
        "claim_to_merge_p50_s": _figure(percentile(cycles, 50), n, minimum, "completed items"),
        "claim_to_merge_p90_s": _figure(percentile(cycles, 90), n, minimum, "completed items"),
        "process_share": _figure(process_share, n, minimum, "completed items"),
        "sequential_review_pair_share": sequential_review_share(ctx, minimum),
        "superseded_pass_s": superseded_pass_hours(ctx),
        "parked_share": parked_share(ctx, minimum),
        "n_completed_items": n,
    }


def effect(ctx: Context, env_key: str | None = None) -> dict:
    """The before/after block: baseline (R-path-merge, for the env class), current, and per
    metric ``delta = current - baseline`` when both are known (None when either is None)."""
    base = baseline(env_key)
    cur = current(ctx)
    rows = []
    for key, label, _lower in METRICS:
        b = base.get(key)
        c = cur[key]["value"]
        rows.append(
            {
                "metric": key,
                "label": label,
                "baseline": b,
                "current": c,
                "delta": c - b if b is not None and c is not None else None,
                "n": cur[key].get("n"),
            }
        )
    return {
        "env_key": env_key or "",
        "baseline": base,
        "current": cur,
        "metrics": rows,
        "next": {
            "cli": "ddflow perf report",
            "mcp": "ddflow_perf action=report",
            "note": "the effect block is shown in the perf report this module feeds",
        },
    }


def has_figures(data: dict) -> bool:
    """True when any metric computed a value (else the answer is exit 2)."""
    return any(r["current"] is not None for r in data["metrics"])


def _dur(seconds: float | None) -> str:
    if seconds is None:
        return "n/a"
    minute = clock.DURATION_UNITS["m"]
    return f"{seconds / 3600:.1f}h" if seconds >= 3600 else (
        f"{seconds / minute:.0f}m" if seconds >= minute else f"{seconds:.0f}s"
    )


def _pct(share: float | None) -> str:
    return f"{share:.0%}" if share is not None else "n/a"


def text(data: dict) -> str:
    """The human block, shared by CLI and MCP: one row per metric, ``baseline -> current
    (delta)``, direction only when both sides are known (lower-is-better for all six)."""
    rows = data["metrics"]
    out = ["workflow-path effect (vs R-path-merge baseline)"]
    for r in rows:
        b, c, d = r["baseline"], r["current"], r["delta"]
        if b is None:
            out.append(f"  {r['label']}: n/a -> {_render_value(r['metric'], c)} (n={r['n']})")
        elif c is None:
            out.append(
                f"  {r['label']}: {_render_value(r['metric'], b)} -> n/a "
                f"(n={r['n']}, not enough data)"
            )
        else:
            sign = "▼" if d < 0 else ("▲" if d > 0 else "=")
            out.append(
                f"  {r['label']}: {_render_value(r['metric'], b)} -> "
                f"{_render_value(r['metric'], c)} ({sign}{_render_value(r['metric'], abs(d))})"
            )
    out.append(f"next: {data['next']['cli']}  |  {data['next']['mcp']}")
    return "\n".join(out)


def _render_value(metric: str, value: float | None) -> str:
    if value is None:
        return "n/a"
    if metric == "process_share" or metric == "parked_share":
        return _pct(value)
    if metric == "sequential_review_pair_share":
        return _pct(value)
    return _dur(value)


__all__ = [
    "BASELINES",
    "METRICS",
    "PARKED_THRESHOLD_S",
    "REVIEW_GATES",
    "baseline",
    "current",
    "effect",
    "has_figures",
    "parked_share",
    "sequential_review_share",
    "superseded_pass_hours",
    "text",
]
