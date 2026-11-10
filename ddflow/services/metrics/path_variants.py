"""The workflow-path variants (WP-path-variants): the paths view's second lens.

From ``facts.collect`` (extends WP-path-collect, no new collector): the directly-follows gate
graph with median gaps, the top ordered gate-sequence variants, the burst overlap (union vs
sum), a pass superseded by a later tree change, and a gate run recorded out of the canonical
order. Pure, stdlib-only (~30 lines of algorithm): no PM4Py.

Research: R-path-merge (canonical order 25% today; superseded passes 119 h in the window).
"""

from __future__ import annotations

import itertools
from collections import defaultdict
from statistics import median

from ...core import clock
from . import paths
from .providers import Context

#: The canonical task lanes in order. D-path-merge-answers lets bookkeeping gates be
#: recorded in one call, so ``research``+``rules``+``implement`` and ``bug_hunt``+``dedupe``
#: are each ONE lane: order is per-lane, not per-gate -- two gates inside the same merged
#: lane are never out of order relative to each other.
LANES: tuple[tuple[str, ...], ...] = (
    ("research", "rules", "implement"),
    ("rubber_duck",),
    ("critic",),
    ("standards",),
    ("ci",),
    ("unit_tests",),
    ("bug_hunt", "dedupe"),
    ("merge",),
)


def _items(ctx: Context) -> dict[str, list]:
    """item -> its gate verdicts in time order."""
    outs: dict[str, list] = defaultdict(list)
    for o in ctx.facts.outcomes:
        outs[o.item].append(o)
    return {item: sorted(rows, key=lambda o: o.at) for item, rows in outs.items()}


def _sequence(rows: list) -> list[str]:
    """The ordered gate names of an item, consecutive repeats collapsed (a re-run is one step)."""
    seq: list[str] = []
    for o in rows:
        if not seq or seq[-1] != o.gate:
            seq.append(o.gate)
    return seq


def _lane_rank() -> dict[str, int]:
    """gate -> its canonical lane position (gates in one merged lane share a rank)."""
    return {gate: i for i, lane in enumerate(LANES) for gate in lane}


def directly_follows(ctx: Context, items: dict | None = None) -> list[dict]:
    """gate -> gate transition counts over the items, and the median gap of each ADJACENT
    occurrence (a re-run or an out-of-order pass keeps its own gap, never a min-of-all)."""
    pairs: dict[tuple[str, str], list[float]] = defaultdict(list)
    for _item, rows in (items if items is not None else _items(ctx)).items():
        for a, b in itertools.pairwise(rows):
            if a.gate != b.gate:
                pairs[(a.gate, b.gate)].append(b.at - a.at)
    return [
        {"a": a, "b": b, "n": len(gaps), "median_gap_s": median(gaps) if gaps else None}
        for (a, b), gaps in sorted(pairs.items())
    ]


def variants(ctx: Context, items: dict | None = None) -> list[dict]:
    """The ordered gate sequences and how often each occurs (top variants first)."""
    counts: dict[str, int] = defaultdict(int)
    items = items if items is not None else _items(ctx)
    for seq in (_sequence(items[item]) for item in items):
        counts[tuple(seq)] += 1
    return [
        {"sequence": list(seq), "n": n} for seq, n in sorted(counts.items(), key=lambda kv: -kv[1])
    ]


def superseded_passes(ctx: Context, items: dict | None = None) -> list[dict]:
    """a PASS on a tree that a LATER outcome on the same item left behind (a different
    tree_sha recorded after it), so the pass counted for a tree that is no longer current."""
    out = []
    for item, rows in (items if items is not None else _items(ctx)).items():
        for i, o in enumerate(rows):
            if o.kind != "passed" or not o.tree_sha:
                continue
            if any(r.tree_sha and r.tree_sha != o.tree_sha for r in rows[i + 1 :]):
                out.append({"item": item, "gate": o.gate, "at": o.at, "tree_sha": o.tree_sha})
    return out


def out_of_order(ctx: Context, items: dict | None = None) -> list[dict]:
    """a gate whose FIRST run came before an EARLIER canonical LANE's first run (it ran ahead
    of a prerequisite lane that had not STARTED yet -- e.g. critic first-ran before
    rubber_duck). A lane has started when its EARLIEST member gate first ran, so two gates of
    one merged lane never count against each other."""
    rank = _lane_rank()
    out = []
    for item, rows in (items if items is not None else _items(ctx)).items():
        first_at: dict[str, float] = {}
        for o in rows:
            if o.gate in rank and o.gate not in first_at:
                first_at[o.gate] = o.at
        lane_first: dict[int, float] = {}
        for i, lane in enumerate(LANES):
            times = [first_at[g] for g in lane if g in first_at]
            if times:
                lane_first[i] = min(times)
        for gate, r in rank.items():
            at = first_at.get(gate)
            if at is None:
                continue
            # an earlier lane whose FIRST run came AFTER this run: this run was ahead of it
            if any(j < r and lane_first[j] > at for j in lane_first):
                out.append({"item": item, "gate": gate, "at": at})
    return out


def overlap(ctx: Context) -> dict:
    """the burst overlap: the summed process time vs its union (what the concurrent view
    saves), as WINDOW TOTALS over the same per-item pairs -- never the sum of two medians of
    different series. ``burst_share`` is the union's fraction of the sum, and is ``None``
    (``unavailable``) when the sibling ``paths`` view has too few samples to state it."""
    data = paths.build(ctx)
    rows = data["paths"]
    sum_s = sum(p["process_sum_s"] for p in rows)
    union_s = sum(p["process_union_s"] for p in rows)
    unavailable = bool(data["unavailable"]) or sum_s == 0
    return {
        "union_s": union_s,
        "sum_s": sum_s,
        "saved_s": sum_s - union_s,
        "burst_share": union_s / sum_s if sum_s and not unavailable else None,
        "n_items": len(rows),
        "unavailable": unavailable,
    }


def build(ctx: Context) -> dict:
    items = _items(ctx)
    return {
        "directly_follows": directly_follows(ctx, items),
        "variants": variants(ctx, items),
        "overlap": overlap(ctx),
        "superseded_passes": superseded_passes(ctx, items),
        "out_of_order": out_of_order(ctx, items),
        "next": {
            "cli": "ddflow paths",
            "mcp": "ddflow_paths",
        },
    }


def has_figures(data: dict) -> bool:
    return bool(data["directly_follows"] or data["variants"])


def text(data: dict) -> str:
    """The human form, shared by CLI and MCP."""
    out = ["workflow-path variants"]
    for r in data["directly_follows"]:
        gap = r["median_gap_s"]
        out.append(
            f"  {r['a']} -> {r['b']}: {r['n']} (median gap {gap:.0f}s)"
            if gap is not None
            else f"  {r['a']} -> {r['b']}: {r['n']}"
        )
    out.append("top variants")
    out += [f"  {' -> '.join(r['sequence'])}: {r['n']}" for r in data["variants"][:8]]
    ov = data["overlap"]
    if not ov.get("unavailable"):
        out.append(
            f"  burst overlap: union {_d(ov['union_s'])} of sum {_d(ov['sum_s'])} ({ov['burst_share']:.0%}, {ov['n_items']} completed item(s))"
        )
    else:
        out.append("  burst overlap: not enough data (unavailable, not zero)")
    out.append(f"  superseded passes: {len(data['superseded_passes'])}")
    out.append(f"  out-of-order runs: {len(data['out_of_order'])}")
    out.append(f"next: {data['next']['cli']}  |  {data['next']['mcp']}")
    return "\n".join(out)


def _d(seconds) -> str:
    if seconds is None:
        return "n/a"
    minute = clock.DURATION_UNITS["m"]
    return f"{seconds / minute:.0f}m" if seconds >= minute else f"{seconds:.0f}s"


__all__ = [
    "build",
    "directly_follows",
    "has_figures",
    "out_of_order",
    "overlap",
    "superseded_passes",
    "variants",
]
