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

import itertools
from collections import defaultdict

from ..metrics import paths as _paths
from ..metrics.providers import Context
from ..metrics.render import dur, pct
from ..metrics.stats import percentile
from ..metrics.summary import completed_spans

#: The one place a duration in hours is named (clock.DURATION_UNITS has no fraction unit).
HOUR = 3600.0

#: The process gates whose verdicts can run concurrently and so form a review PAIR.
REVIEW_GATES = ("rubber_duck", "critic")

#: A verdict that RAN the gate (a skip or unavailable did not): the kinds that form a pair.
RAN_KINDS = ("passed", "failed", "partial")

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
    ("superseded_pass_s", "superseded-pass hours per merged item", True),
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
        # 119 h of superseded passes over the corpus, PER item (a rate, window-independent:
        # comparing a raw window total against a fixed total mislabels a longer window).
        "superseded_pass_s": 119 * HOUR / 1322,
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
    """The summed wall time of timed PASSED verdicts a later outcome on the same item left
    behind (a different ``tree_sha`` recorded after it) -- the pass counted for a tree that
    is no longer current, PER completed item (a rate: a raw total grows with the window and
    would misread as a regression against the fixed corpus baseline). Mirrors
    ``path_variants.superseded_passes`` but weights by ``elapsed_s``. A superseded pass with
    no recorded time adds a count but no seconds. Only COMPLETED (in-scope) items count: the
    metric is per merged item, and a superseded pass on an unfinished item is not a cost of
    merged work. Returns the raw facts; the per-item value and floor are applied in
    :func:`current`."""
    spans = completed_spans(ctx)
    by_item: dict[str, list] = defaultdict(list)
    done = {s.item for s in spans}
    for o in ctx.facts.outcomes:
        if o.item in done:
            by_item[o.item].append(o)
    seconds = 0.0
    count = 0
    for rows in by_item.values():
        ordered = sorted(rows, key=lambda o: o.at)
        for i, o in enumerate(ordered):
            if o.kind != "passed" or not o.tree_sha:
                continue
            if any(r.tree_sha and r.tree_sha != o.tree_sha for r in ordered[i + 1 :]):
                count += 1
                if o.elapsed_s is not None:
                    seconds += o.elapsed_s
    return {"seconds": seconds, "count": count, "n_items": len(spans)}


def _classify(r, c) -> str:
    """How ONE rubber_duck/critic pair ran: ``combined`` (one command recorded both),
    ``concurrent`` (both timed and their process windows overlap), ``sequential`` else."""
    if "rubber_duck" in c.combined_gates or "critic" in r.combined_gates:
        return "combined"
    ri, ci = _interval(r), _interval(c)
    if ri is not None and ci is not None and ri[0] < ci[1] and ci[0] < ri[1]:
        return "concurrent"
    return "sequential"


def _latest(rows: list, gate: str):
    return max((o for o in rows if o.gate == gate), key=lambda o: o.at, default=None)


def sequential_review_share(ctx: Context, minimum: int) -> dict:
    """The share of rubber_duck/critic review PAIRS that ran SEQUENTIALLY: neither combined
    into one command nor overlapping. A pair is the latest rubber_duck and the latest
    critic verdict on one tree; ``combined`` when either verdict's ``combined_gates`` names
    the other (D-path-merge-answers), ``concurrent`` when both timed verdicts' process
    windows overlap, ``sequential`` otherwise (an untimed single-run verdict is presumed
    non-overlapping). Below the pair floor the share is None, the breakdown is not."""
    by_tree: dict[tuple[str, str], list] = defaultdict(list)
    for s in completed_spans(ctx):
        spanned = {o for o in ctx.facts.outcomes if o.item == s.item}
        for o in spanned:
            if o.gate in REVIEW_GATES and o.kind in RAN_KINDS:
                by_tree[(s.item, o.tree_sha)].append(o)
    counts: dict[str, int] = {"sequential": 0, "concurrent": 0, "combined": 0}
    for rows in by_tree.values():
        r = _latest(rows, "rubber_duck")
        c = _latest(rows, "critic")
        if r is not None and c is not None:
            counts[_classify(r, c)] += 1
    total = sum(counts.values())
    value = counts["sequential"] / total if total else None
    return {
        "n": total,
        **counts,
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
        if any(b - a > PARKED_THRESHOLD_S for a, b in itertools.pairwise(ordered)):
            parked += 1
    value = parked / n if n else None
    return _figure(value, n, minimum, what="completed items") | {"parked": parked}


def current(ctx: Context) -> dict[str, dict]:
    """The six current numbers over the window. Rates and percentiles carry the sample
    floor; the superseded hour-sum is a per-item rate, floored on completed items."""
    minimum = int(ctx.cfg.metrics.min_samples)
    spans = completed_spans(ctx)
    n = len(spans)
    paths = _paths.build(ctx)["paths"]
    union = sum(p["process_union_s"] for p in paths)
    cycles = [s.cycle_s for s in spans]  # completed_spans keeps only non-None cycle_s
    cycle = sum(cycles)
    gate_hours = union / n if n else None
    process_share = union / cycle if cycle else None
    superseded = superseded_pass_hours(ctx)
    sup_value = superseded["seconds"] / superseded["n_items"] if superseded["n_items"] else None
    return {
        "gate_hours_per_merged_s": _figure(gate_hours, n, minimum, "completed items"),
        "claim_to_merge_p50_s": _figure(percentile(cycles, 50), n, minimum, "completed items"),
        "claim_to_merge_p90_s": _figure(percentile(cycles, 90), n, minimum, "completed items"),
        "process_share": _figure(process_share, n, minimum, "completed items"),
        "sequential_review_pair_share": sequential_review_share(ctx, minimum),
        "superseded_pass_s": _figure(sup_value, superseded["n_items"], minimum, "completed items")
        | {"superseded_passes": superseded["count"]},
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


_SHARE_METRICS = ("process_share", "sequential_review_pair_share", "parked_share")


def _render_value(metric: str, value: float | None) -> str:
    """One figure in its unit: shares as a percent, the rest as a duration; None is ``n/a``."""
    return pct(value) if metric in _SHARE_METRICS else dur(value)


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
