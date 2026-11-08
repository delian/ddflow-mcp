"""``ddflow metrics paths``: each item's workflow path, with process vs record steps.

A pure fold over ``facts.collect`` (extends PM-cycle-split): for every completed item, the
ordered sequence of gate steps -- a PROCESS (a gate that ran a tool: ``rubber_duck``,
``critic``, ``ci``, ``unit_tests``, ``standards`` with a recorded ``elapsed_s``), or a RECORD
(a gate that only recorded an outcome: ``research``, ``rules``, ``implement``, ``bug_hunt``,
``dedupe``, or a review/ci/unit_tests/standards verdict that ran nothing and was covered by
another gate). A process window may overlap another that ran concurrently: the path reports
the UNION of concurrent process seconds, not the sum (``metrics stages`` counts concurrent
gates twice). No new event is read.
"""

from __future__ import annotations

from collections import defaultdict

from .facts import Outcome
from .providers import Context
from .stats import stat
from .summary import completed_spans, gate_durations

#: Gates whose verdict RAN a process (a tool or reviewer with a duration). ``standards``
#: runs roborev, hence a process only when it recorded an ``elapsed_s``.
PROCESS_GATES = ("rubber_duck", "critic", "ci", "unit_tests", "standards")
#: Gates whose verdict only RECORDS an outcome (nothing runs behind them). A process gate
#: recorded without running (covered by another gate) reads as a record, not a process.
RECORD_GATES = frozenset({"research", "rules", "implement", "bug_hunt", "dedupe"})


def step_of(gate: str, ran: bool) -> str:
    """``process`` when the gate ran something, ``record`` when it only recorded."""
    return "process" if gate in PROCESS_GATES and ran else "record"


def _union_s(windows: list[tuple[float, float]]) -> float:
    """The union of ``[start, end)`` windows; concurrent seconds counted once."""
    total, edge = 0.0, float("-inf")
    for start, end in sorted(windows):
        start = max(start, edge)  # noqa: PLW2901
        if end > start:
            total += end - start
            edge = end
    return total


def _steps(item: str, outs: list[Outcome], ran: set[int]) -> list[dict]:
    steps = []
    for o in outs:
        steps.append(
            {
                "at": o.at,
                "gate": o.gate,
                "kind": o.kind,
                "step": step_of(o.gate, id(o) in ran),
                "elapsed_s": o.elapsed_s,
                "round": o.round,
                "tree_sha": o.tree_sha,
                "review_id": o.review_id,
                "combined_gates": list(o.combined_gates),
                "note": o.note,
            }
        )
    return steps


def _bursts(windows: list[tuple[float, float]]) -> list[dict]:
    """The maximal sets of (transitively) overlapping process windows: each burst is one
    ``(start, end)`` union with the count of runs in it and its union seconds."""
    bursts: list[dict] = []
    for start, end in sorted(windows):
        if bursts and start < bursts[-1]["end"]:
            bursts[-1]["end"] = max(bursts[-1]["end"], end)
            bursts[-1]["runs"] += 1
        else:
            bursts.append({"start": start, "end": end, "runs": 1})
    for b in bursts:
        b["seconds"] = b["end"] - b["start"]
    return bursts


def _item(ctx: Context, item: str, outs: list, durations: list[tuple[Outcome, float]]) -> dict:
    """One item's path: ordered process windows, their bursts, and the summed/union time.
    The steps read the SAME ``ran`` set the timing does (an outcome ``gate_durations`` gave a
    window to is a process), so ``step == "process"`` never contradicts ``n_process``."""
    outs = sorted(outs, key=lambda o: o.at)
    ran = {id(o) for o, _ in durations}
    windows = [(o.at - secs, o.at) for o, secs in durations]
    return {
        "item": item,
        "process_sum_s": sum(secs for _, secs in durations),
        "process_union_s": _union_s(windows),
        "n_process": len(durations),
        "bursts": _bursts(windows),
        "steps": _steps(item, outs, ran),
    }


def build(ctx: Context) -> dict:
    """The ordered paths as plain data. ``process_union_s`` is the burst-corrected process
    time (concurrent gates counted once); ``process_sum_s`` is what ``metrics stages`` sums."""
    minimum = int(ctx.cfg.metrics.min_samples)
    runs_of: dict[str, list[tuple[Outcome, float]]] = defaultdict(list)
    for o, secs in gate_durations(ctx):
        runs_of[o.item].append((o, secs))
    outs_of: dict[str, list[Outcome]] = defaultdict(list)
    for o in ctx.facts.outcomes:
        outs_of[o.item].append(o)
    paths = [
        _item(ctx, s.item, outs_of[s.item], runs_of.get(s.item, ())) for s in completed_spans(ctx)
    ]
    unions = [p["process_union_s"] for p in paths]
    saved = [p["process_sum_s"] - p["process_union_s"] for p in paths]
    unavailable: dict[str, str] = {}
    if len(unions) < max(minimum, 1):
        unavailable["process_union"] = f"{len(unions)} items, need {minimum}"
    return {
        "items": len(paths),
        "process_union": {
            "union": stat(unions, minimum),
            #: summed minus union == the concurrent double-count: how much ``stages`` over-counts.
            "saved_by_union": stat(saved, minimum),
        },
        "paths": paths,
        "unavailable": unavailable,
        "next": {
            "cli": "ddflow metrics paths",
            "mcp": "ddflow_metrics view=paths",
            "note": "process_union_s counts concurrent gates once; stages sums them",
        },
    }


def has_figures(data: dict) -> bool:
    return data["process_union"]["union"]["p50_s"] is not None


def text(data: dict) -> str:
    u = data["process_union"]
    out = [
        "workflow paths (process vs record steps)",
        f"  process time per item: p50 union {_dur(u['union']['p50_s'])}"
        f"  p90 {_dur(u['union']['p90_s'])} (n={u['union']['n']}, concurrent gates counted once)",
        f"  summed-vs-union saves p50 {_dur(u['saved_by_union']['p50_s'])} per item",
        f"  {data['items']} completed item(s) with a path",
    ]
    if data["unavailable"]:
        out += ["", "unavailable (not zero):"]
        out += [f"  {k}: {why}" for k, why in sorted(data["unavailable"].items())]
    nxt = data["next"]
    out.append(f"next: {nxt['cli']}  |  {nxt['mcp']}")
    return "\n".join(out)


def _dur(seconds):
    if seconds is None:
        return "n/a"
    return f"{seconds / 60:.0f}m" if seconds >= 60 else f"{seconds:.0f}s"
