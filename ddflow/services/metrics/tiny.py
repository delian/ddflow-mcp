"""``ddflow metrics tiny``: the evidence behind the tiny-fix fast track (D-fast-track-answers).

Derived from the facts the log gives (``facts.collect``, plus the triaged review findings) and
the ``[fasttrack]`` knobs that define a tiny-cold change. It answers: how many completed items
met the tiny-cold limits and at what finding-and-rework cost, and what each gate cost. A figure
the log cannot support -- the file-count limits, a later fix that touched the same file, bundle
savings -- is named under ``not_measured``, never a zero.
"""

from __future__ import annotations

from collections import defaultdict

from .providers import Context
from .stats import rate, stat
from .summary import completed_spans, gate_durations

#: Figures the log does not support yet, and what would give them (the same honest form
#: `metrics stages` uses): named, never zeroed.
NOT_MEASURED = {
    "tiny_cold_files": "the file-count limits of a tiny-cold change are judged over the actual "
    "diff by git numstat (FT-guard); the log records only changed lines, so the metrics view "
    "cannot name an item by its file count yet",
    "same_file_refix": "a later fix touching the same file needs the per-file change history "
    "(git numstat) the log does not fold; the later-bug rate by bucket is the log-only part",
    "bundle_savings": "bundling has never run (FT-bundle ships it): the saved gate-seconds "
    "are not known until the first bundle lands and are reported then, not estimated now",
}


def _size_bucket(lines: int, ctx: Context) -> str:
    """The bucket of a completed item's size, tiny-cold first (D-fast-track-answers)."""
    if not lines:  # 0: the tree was already committed when measured, so it says nothing
        return "(unknown size)"
    tiny, small = (
        ctx.cfg.fasttrack.tiny_cold_max_source_lines,
        ctx.cfg.metrics.stage_size_small,
    )
    large = ctx.cfg.metrics.stage_size_large
    if lines <= tiny:
        return f"tiny-cold (<={tiny} lines)"
    if lines <= small:
        return f"small (<={small} lines)"
    return f"medium (<={large} lines)" if lines <= large else f"large (>{large} lines)"


def _size_of(ctx: Context, outs: list) -> int:
    """The size an item shipped as: the last gate verdict's recorded changed lines (the size
    nearest completion), 0 when none ever did. An item that shrank between verdicts is
    bucketed by its last recorded size, not its largest (roborev standards finding)."""
    sizes = [o.changed_lines for o in sorted(outs, key=lambda o: o.at) if o.changed_lines]
    return sizes[-1] if sizes else 0


def _tiny_cold(ctx: Context, minimum: int, spans, outs_of) -> dict:
    """Completed items with a MEASURED size; an unmeasured size is never a failed limit."""
    limit = ctx.cfg.fasttrack.tiny_cold_max_source_lines
    measured = [s for s in spans if _size_of(ctx, outs_of.get(s.item, ())) > 0]
    tiny = [
        s for s in measured if _size_of(ctx, outs_of.get(s.item, ())) <= limit
    ]
    return {
        "count": len(tiny),
        "total": len(measured),
        "unmeasured": len(spans) - len(measured),
        "scope": f"met the source-line limit (<= {limit} lines); the file-count limits are not_measured",
        "rate": rate(len(tiny), len(measured), minimum),
    }


def _confirmation(ctx: Context, minimum: int) -> dict:
    """Confirmed HIGH/MEDIUM findings over every triaged finding: do lightened reviews miss a
    finding the author confirms? (D-fast-track-answers' falsifier the operator watches.)"""
    triaged = [f for f in ctx.facts.findings if ctx.in_scope(f.item, f.at)]
    confirmed = [f for f in triaged if f.verdict == "confirmed" and f.severity in ("HIGH", "MEDIUM")]
    return {
        "triaged": len(triaged),
        "confirmed_high_medium": len(confirmed),
        "rate": rate(len(confirmed), len(triaged), minimum),
    }


def _by_size(ctx: Context, minimum: int, spans, outs_of) -> list[dict]:
    """later-bug rate per size bucket: of the items that completed, how many were hit by a bug
    filed after they completed (the rework the summary reports, split by bucket)."""
    buckets: dict[str, dict[str, int]] = defaultdict(lambda: {"items": 0, "later": 0})
    for s in spans:
        b = buckets[_size_bucket(_size_of(ctx, outs_of.get(s.item, ())), ctx)]
        b["items"] += 1
        if any(item == s.item and at > (s.completed_at or 0) for item, at in ctx.facts.bugs):
            b["later"] += 1
    return [
        {
            "bucket": bucket,
            "items": b["items"],
            "later": b["later"],
            "later_bug_rate": rate(b["later"], b["items"], minimum),
        }
        for bucket, b in sorted(buckets.items())
    ]


def _gate_seconds(ctx: Context, minimum: int) -> list[dict]:
    per_gate: dict[str, list[float]] = defaultdict(list)
    for o, secs in gate_durations(ctx):
        per_gate[o.gate].append(secs)
    return [{"gate": g, **stat(v, minimum)} for g, v in sorted(per_gate.items())]


def build(ctx: Context) -> dict:
    """The tiny-fix fast-track scoreboard as plain data."""
    minimum = int(ctx.cfg.metrics.min_samples)
    spans = completed_spans(ctx)
    outs_of: dict[str, list] = defaultdict(list)
    for o in ctx.facts.outcomes:
        outs_of[o.item].append(o)
    gate_seconds = _gate_seconds(ctx, minimum)
    sizes = _by_size(ctx, minimum, spans, outs_of)
    unavailable: dict[str, str] = {}
    if not any(r["p50_s"] is not None for r in gate_seconds):
        unavailable["gate_seconds"] = f"no gate with {minimum} timed runs"
    if sizes and not any(r["later_bug_rate"] is not None for r in sizes):
        unavailable["by_size"] = f"fewer than {minimum} completed items in any bucket"
    return {
        "tiny_cold": _tiny_cold(ctx, minimum, spans, outs_of),
        "gate_seconds": gate_seconds,
        "finding_rate": _confirmation(ctx, minimum),
        "by_size": sizes,
        "not_measured": dict(NOT_MEASURED),
        "unavailable": unavailable,
        "next": {
            "cli": "ddflow metrics tiny",
            "mcp": "ddflow_metrics view=tiny",
            "note": (
                "fallback rate by tier stays separate: ddflow metrics --group-by tier"
            ),
        },
    }


def has_figures(data: dict) -> bool:
    """Whether any figure could be computed, else the answer is exit 2."""
    return (
        data["tiny_cold"]["rate"] is not None
        or any(r["p50_s"] is not None for r in data["gate_seconds"])
        or data["finding_rate"]["rate"] is not None
        or any(r["later_bug_rate"] is not None for r in data["by_size"])
    )


def text(data: dict) -> str:
    """The human form, shared by CLI and MCP."""
    tiny = data["tiny_cold"]
    out = [
        "tiny-fix fast track (derived from the event log)",
        f"  tiny-cold: {tiny['count']} of {tiny['total']} measured completed items met the "
        f"source-line limit ({_pct(tiny['rate'])})",
    ]
    if tiny.get("unmeasured"):
        out.append(f"  {tiny['unmeasured']} completed item(s) with no measured size, not counted")
    out.append("time per gate")
    out += [
        f"  {r['gate']:<14} n={r['n']:<5} p50 {_dur(r['p50_s'])}  p90 {_dur(r['p90_s'])}"
        f"  max {_dur(r['max_s'])}"
        for r in data["gate_seconds"]
    ]
    fr = data["finding_rate"]
    out.append(
        f"  confirmed HIGH/MEDIUM findings: {fr['confirmed_high_medium']} of {fr['triaged']} "
        f"triaged ({_pct(fr['rate'])})"
    )
    out.append("later-bug rate by size bucket")
    out += [
        f"  {r['bucket'][:30]:<30} {r['items']} items, later bug {_pct(r['later_bug_rate'])}"
        for r in data["by_size"]
    ]
    if data["not_measured"]:
        out += ["", "not measured (not zero):"]
        out += [f"  {k}: {why}" for k, why in sorted(data["not_measured"].items())]
    if data["unavailable"]:
        out += ["", "unavailable (not zero):"]
        out += [f"  {k}: {why}" for k, why in sorted(data["unavailable"].items())]
    nxt = data["next"]
    out.append(f"next: {nxt['cli']}  |  {nxt['mcp']}  ({nxt['note']})")
    return "\n".join(out)


def _dur(seconds):
    if seconds is None:
        return "n/a"
    if seconds >= 60:
        return f"{seconds / 60:.0f}m"
    return f"{seconds:.0f}s" if seconds >= 1 else f"{seconds:.1f}s"


def _pct(value):
    if value is None:
        return "n/a"
    return f"{value * 100:.1f}%"
