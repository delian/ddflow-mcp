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
from .providers import Context

#: The canonical gate order (a task pipeline); a gate out of order is one that ran before a
#: prerequisite. D-path-merge-answers merges bookkeeping gates (research+rules+implement,
#: bug_hunt+dedupe), so the canonical order is per-lane, not per-gate.
CANON = (
    "research",
    "rules",
    "implement",
    "rubber_duck",
    "critic",
    "standards",
    "ci",
    "unit_tests",
    "bug_hunt",
    "dedupe",
    "merge",
)


def _items(ctx: Context) -> dict[str, list]:
    """item -> its gate verdicts in time order."""
    from collections import defaultdict

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


def directly_follows(ctx: Context) -> list[dict]:
    """gate -> gate transition counts over the items, and the median gap of each ADJACENT
    occurrence (a re-run or an out-of-order pass keeps its own gap, never a min-of-all)."""
    pairs: dict[tuple[str, str], list[float]] = defaultdict(list)
    for _item, rows in _items(ctx).items():
        for a, b in itertools.pairwise(rows):
            if a.gate != b.gate:
                pairs[(a.gate, b.gate)].append(b.at - a.at)
    return [
        {"a": a, "b": b, "n": len(gaps), "median_gap_s": median(gaps) if gaps else None}
        for (a, b), gaps in sorted(pairs.items())
    ]


def variants(ctx: Context) -> list[dict]:
    """The ordered gate sequences and how often each occurs (top variants first)."""
    counts: dict[str, int] = defaultdict(int)
    for item in _items(ctx):
        counts[tuple(_sequence(_items(ctx)[item]))] += 1
    return [
        {"sequence": list(seq), "n": n} for seq, n in sorted(counts.items(), key=lambda kv: -kv[1])
    ]


def superseded_passes(ctx: Context) -> list[dict]:
    """a PASS superseded by a later tree change on the same item (the later run re-reviewed a
    moved tree, so the earlier pass counted for nothing)."""
    out = []
    for item, rows in _items(ctx).items():
        by_gate: dict[str, list] = defaultdict(list)
        for o in rows:
            by_gate[o.gate].append(o)
        for gate, runs in by_gate.items():
            for i, o in enumerate(runs):
                if o.kind == "passed" and any(
                    r.tree_sha and r.tree_sha != o.tree_sha for r in runs[i + 1 :]
                ):
                    out.append({"item": item, "gate": gate, "at": o.at, "tree_sha": o.tree_sha})
    return out


def out_of_order(ctx: Context) -> list[dict]:
    """a gate run recorded before an EARLIER canonical step on the same item (it ran ahead of
    its prerequisite -- e.g. critic before rubber_duck)."""
    rank = {g: i for i, g in enumerate(CANON)}
    out = []
    for item, rows in _items(ctx).items():
        rrows = [(o.at, o.gate) for o in rows if o.gate in rank]
        for i, (at, gate) in enumerate(rrows):
            if any(rank[g] < rank[gate] for t2, g in rrows[i + 1 :]):
                out.append({"item": item, "gate": gate, "at": at})
    return out


def overlap(ctx: Context) -> dict:
    """the burst overlap: the summed process time vs its union (what the concurrent view saves),
    and the overlap share (the union's fraction of the sum)."""
    from . import paths

    u = paths.build(ctx)["process_union"]
    union_s = u.get("union", {}).get("p50_s")
    saved_s = u.get("saved_by_union", {}).get("p50_s")
    total = union_s + saved_s if union_s is not None and saved_s is not None else None
    return {
        "union_p50_s": union_s,
        "saved_p50_s": saved_s,
        "sum_p50_s": total,
        "burst_share": union_s / total if union_s and total else None,
    }


def build(ctx: Context) -> dict:
    return {
        "directly_follows": directly_follows(ctx),
        "variants": variants(ctx),
        "overlap": overlap(ctx),
        "superseded_passes": superseded_passes(ctx),
        "out_of_order": out_of_order(ctx),
        "next": {
            "cli": "ddflow metrics paths --variants",
            "mcp": "ddflow_metrics view=paths variants=true",
        },
    }


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
    if ov.get("burst_share") is not None:
        out.append(
            f"  burst overlap: union {_d(ov['union_p50_s'])} of sum {_d(ov['sum_p50_s'])} ({ov['burst_share']:.0%})"
        )
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
    "out_of_order",
    "overlap",
    "superseded_passes",
    "variants",
]
