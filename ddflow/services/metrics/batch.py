"""``ddflow metrics batch``: the size-bucket tables behind batched gates.

Reproduces the measured tables of ``docs/ddflow/research/batch-gates.md`` section 1 from the
log alone: review runs and minutes bucketed by each item's changed lines, findings per run
and the share of runs that produced one, findings confirmed/refuted by size, and the review
gate-lock wait (the ``waited_s`` evidence ``fix-B1d51fb9da1`` records). An item's size is the
largest changed-line figure any of its recorded verdicts named (the same proxy the ``stages``
view uses); an item whose evidence never named one is grouped as unknown size, never as small.

Nothing here reads a checkout: a figure the log cannot say is ``None``, never a zero, and the
``unavailable`` map names it.
"""

from __future__ import annotations

from collections import defaultdict

from ...core import progress as PR
from .providers import Context
from .stats import rate, stat
from .summary import RAN

#: The fixed size buckets of the research (lines changed, code plus tests). Gate run cost is
#: a fixed price plus a slow tail, so the buckets are where the "review per X lines" economics
#: show; they are not knobs (a company tunes the batch triggers, not the measurement).
SIZE_BUCKETS: tuple[tuple[str, int | None, int | None], ...] = (
    ("< 50", None, 50),
    ("50-149", 50, 150),
    ("150-399", 150, 400),
    ("400-999", 400, 1000),
    ("1000+", 1000, None),
)
UNKNOWN_SIZE = "(unknown size)"

#: The review gates whose runs are `review runs` here (shared with `stages`/`summary`).
REVIEW_GATES = PR.REVIEW_GATES


def bucket_of(lines: int) -> str:
    """The bucket ``lines`` falls in; ``lines`` of 0 or None is the unknown bucket (0 means
    the tree was already committed when measured, so it says nothing about the item size)."""
    if not lines:
        return UNKNOWN_SIZE
    for name, lo, hi in SIZE_BUCKETS:
        if (lo is None or lines >= lo) and (hi is None or lines < hi):
            return name
    return UNKNOWN_SIZE


def _item_size(ctx: Context) -> dict[str, int | None]:
    """item -> its changed lines (the largest figure any of its verdicts recorded); None when
    no verdict named one. Read once so every table buckets the same size."""
    size: dict[str, int | None] = {}
    for o in ctx.facts.outcomes:
        if o.changed_lines:
            size[o.item] = max(size.get(o.item) or 0, o.changed_lines)
    return size


def _runs_by_bucket(ctx: Context, size: dict[str, int | None]) -> dict[str, list[dict]]:
    """One entry per (size bucket): review runs with their elapsed time and whether a finding
    was triaged against that run (the item + gate a finding names)."""
    bucket_runs: dict[str, list[dict]] = defaultdict(list)
    # A run "had a finding" when the item+gate pair of the run has a triaged finding.
    with_finding: set[tuple[str, str]] = {(f.item, f.gate) for f in ctx.facts.findings}
    for o in ctx.facts.outcomes:
        if o.gate not in REVIEW_GATES or o.kind not in RAN or not ctx.in_scope(o.item, o.at):
            continue
        r = {"elapsed_s": o.elapsed_s, "wait_s": o.waited_s, "finding": (o.item, o.gate) in with_finding}
        bucket_runs[bucket_of(size.get(o.item))].append(r)
    return bucket_runs


def _findings_by_bucket(ctx: Context, size: dict[str, int | None]) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for f in ctx.facts.findings:
        out[bucket_of(size.get(f.item))][f.verdict or "unverdicted"] += 1
    return out


def build(ctx: Context, group_by: str = "") -> dict:
    """The batch tables as plain data. Exactly one ``group_by`` (``size``) is accepted so the
    CLI's strict choices hold; any other value is a bad call (ValueError)."""
    if group_by not in ("", "size"):
        raise ValueError("the batch view groups by size only")
    minimum = int(ctx.cfg.metrics.min_samples)
    size = _item_size(ctx)
    by_bucket = {name: [] for name, _lo, _hi in SIZE_BUCKETS}
    by_bucket[UNKNOWN_SIZE] = []
    for item in set(ctx.facts.spans) | set(size):
        by_bucket[bucket_of(size.get(item))].append(item)

    run_buckets = _runs_by_bucket(ctx, size)
    finding_buckets = _findings_by_bucket(ctx, size)

    rows = []
    for name, _lo, _hi in (*SIZE_BUCKETS, (UNKNOWN_SIZE, None, None)):
        runs = run_buckets.get(name, [])
        elapsed = stat([r["elapsed_s"] for r in runs if r["elapsed_s"] is not None], minimum)
        waited = stat([r["wait_s"] for r in runs if r["wait_s"] is not None], minimum)
        finds = finding_buckets.get(name, {})
        n = sum(finds.values())
        rows.append(
            {
                "bucket": name,
                "items": len(by_bucket[name]),
                "runs": len(runs),
                "elapsed": elapsed,
                "lock_wait": waited,
                "findings": n,
                "findings_per_run": rate(n, len(runs), minimum),
                "runs_with_finding": rate(
                    sum(1 for r in runs if r["finding"]), len(runs), minimum
                ),
                "confirmed": finds.get("confirmed", 0),
                "refuted": finds.get("refuted", 0),
                "confirmed_rate": rate(
                    finds.get("confirmed", 0),
                    finds.get("confirmed", 0) + finds.get("refuted", 0),
                    minimum,
                ),
            }
        )

    unavailable: dict[str, str] = {}
    if not any(r["findings_per_run"] is not None for r in rows):
        unavailable["findings_per_run"] = f"no size bucket with {minimum} review runs"
    tiny = [r for r in rows if r["runs"] and r["elapsed"]["n"] < max(minimum, 1)]
    for r in tiny:
        unavailable[f"elapsed:{r['bucket']}"] = f"{r['elapsed']['n']} timed runs, need {minimum}"
    return {"group_by": "size", "buckets": rows, "unavailable": unavailable}


def has_figures(data: dict) -> bool:
    """Whether any bucket has any figure (else the answer is exit 2 'not enough data')."""
    return any(
        r["elapsed"]["p50_s"] is not None
        or r["findings_per_run"] is not None
        or r["runs_with_finding"] is not None
        or r["confirmed_rate"] is not None
        for r in data["buckets"]
    )


def text(data: dict) -> str:
    """The human form, shared by CLI and MCP."""
    from ...core import clock

    def secs(v: float | None) -> str:
        if v is None:
            return "n/a"
        minute = clock.DURATION_UNITS["m"]
        return f"{v:.0f}s" if v < minute else f"{v / minute:.0f}m"

    lines = ["batch tables (review runs and findings by item size; n = review runs)"]
    for r in data["buckets"]:
        lines.append(
            f"  {r['bucket']:>9}  items {r['items']}  runs {r['runs']}  "
            f"elapsed p50 {secs(r['elapsed']['p50_s'])}  "
            f"findings/run {r['findings_per_run'] if r['findings_per_run'] is None else round(r['findings_per_run'], 2)}  "
            f"with a finding {_pct(r['runs_with_finding'])}  "
            f"confirmed {_pct(r['confirmed_rate'])}"
        )
    if data["unavailable"]:
        lines.append("unavailable (not zero):")
        lines += [f"  {k}: {why}" for k, why in sorted(data["unavailable"].items())]
    return "\n".join(lines)


def _pct(v: float | None) -> str:
    return "n/a" if v is None else f"{v * 100:.0f}%"
