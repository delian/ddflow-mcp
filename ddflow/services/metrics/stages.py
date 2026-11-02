"""``ddflow metrics stages``: where an item's claim-to-merge time goes.

Little's law: the stage that grows with parallelism is lease and merge wait, the stage that
grows with suite size is gate time. Each item's timeline is cut from the facts the log already
gives (``facts.collect``; nothing is folded a second way) into:

* ``queue_wait``     ready (dependencies done) to first claim;
* ``lease_wait``     first `lease.waiting` of the item to its first claim (a slice of the queue wait);
* ``implement``      first claim to the first `gate.started` (unmeasured when none was recorded);
* ``gate:<name>``    per gate, run time with the host-lock wait (``waited_s``) taken out, and the wait itself;
* ``review_wait``    between review rounds: first review start to last review verdict, less the time
                     inside the reviews (items with two or more timed review rounds only);
* ``merge_wait``     last gate verdict (other than ``merge``) to `worktree.merged`;
* ``claim_to_merge`` first claim to first `worktree.merged`;
* ``rework_rounds``  failed or partial review verdicts; ``gate_reruns`` runs of a gate beyond its first.

An item with no sample for a stage adds none: a missing figure is never a zero. Stages whose
events ddflow does not emit yet are named under ``not_measured``, not zero.
"""

from __future__ import annotations

from collections import defaultdict

from ...core import progress as PR
from .facts import Outcome, Span
from .providers import Context
from .stats import stat
from .summary import RAN, gate_durations

GROUPS = ("phase", "kind", "size", "agent")
UNKNOWN_SIZE = "(unknown size)"
#: Stages that need events ddflow does not record yet, and what would give them.
NOT_MEASURED = {
    "claim_refusals": "ddflow records no event for a refused claim that did not queue; "
    "P-observe.telemetry adds it",
    "human_wait": "ddflow records no event for time spent waiting on a person; "
    "P-observe.telemetry adds it",
}
#: stage -> unit of its statistic (the key suffix: p50_s, p50_rounds, p50_runs).
UNITS = {"rework_rounds": "rounds", "gate_reruns": "runs"}
STAGES = (
    "queue_wait",
    "lease_wait",
    "implement",
    "gate_run",
    "gate_wait",
    "review_wait",
    "merge_wait",
    "claim_to_merge",
    "rework_rounds",
    "gate_reruns",
)


def _size(lines: int | None, ctx: Context) -> str:
    if not lines:  # 0: the tree was already committed when measured, so it says nothing
        return UNKNOWN_SIZE
    small, large = ctx.cfg.metrics.stage_size_small, ctx.cfg.metrics.stage_size_large
    if lines <= small:
        return f"small (<={small} lines)"
    return f"medium (<={large} lines)" if lines <= large else f"large (>{large} lines)"


def _group_of(dim: str, span: Span, lines: int | None, ctx: Context) -> str:
    if dim == "phase":
        return (PR.phase_of(ctx.facts.state, span.item) if ctx.facts.state else "") or "(none)"
    if dim == "kind":
        return span.kind or "(unknown)"
    if dim == "agent":
        return span.claims[0][1] or "(unknown)" if span.claims else "(unknown)"
    if dim == "size":
        return _size(lines, ctx)
    return "(all)"


def _review_wait(runs: list[tuple[Outcome, float]]) -> float | None:
    reviews = [(o, d) for o, d in runs if o.gate in PR.REVIEW_GATES]
    if len(reviews) <= 1:  # a gap between rounds needs two rounds
        return None
    first = min(o.at - d for o, d in reviews)
    last = max(o.at for o, _ in reviews)
    return max(last - first - sum(d for _, d in reviews), 0.0)


def _waits(span: Span, ctx: Context) -> dict[str, float]:
    """queue_wait and lease_wait: what the item waited for before its first claim."""
    claim, got = span.first_claim, {}
    if span.ready_at is not None and claim >= span.ready_at:
        got["queue_wait"] = claim - span.ready_at
    queued = [t for t in ctx.facts.waits.get(span.item, ()) if t <= claim]
    if queued:
        got["lease_wait"] = claim - min(queued)
    return got


def _first_gate(span: Span, outs: list[Outcome], ctx: Context) -> dict[str, float]:
    """implement: first claim to the first gate run starting. Unmeasured when no run recorded
    a start, or when a verdict landed before the first start (an earlier run whose start
    is missing would make the boundary a guess)."""
    starts = [t for t in ctx.facts.starts_of(span.item) if t >= span.first_claim]
    if not starts:
        return {}
    first = min(starts)
    if any(span.first_claim <= o.at < first for o in outs):
        return {}
    return {"implement": first - span.first_claim}


def _gates(runs: list[tuple[Outcome, float]]) -> dict[str, float]:
    if not runs:
        return {}
    waited = [min(o.waited_s or 0.0, d) for o, d in runs]
    got = {"gate_run": sum(d for _, d in runs) - sum(waited)}
    if any(o.waited_s is not None for o, _ in runs):
        got["gate_wait"] = sum(waited)
    rw = _review_wait(runs)
    return {**got, **({"review_wait": rw} if rw is not None else {})}


def _merge(span: Span, outs: list[Outcome], ctx: Context) -> dict[str, float]:
    claim = span.first_claim
    merged = min((t for t in ctx.facts.merges.get(span.item, ()) if t >= claim), default=None)
    if merged is None:
        return {}
    got = {"claim_to_merge": merged - claim}
    before = [o.at for o in outs if o.gate != "merge" and o.at <= merged]
    return {**got, **({"merge_wait": merged - max(before)} if before else {})}


def _rework(outs: list[Outcome]) -> dict[str, float]:
    got: dict[str, float] = {}
    reviews = [o for o in outs if o.gate in PR.REVIEW_GATES and o.kind in RAN]
    if reviews:
        got["rework_rounds"] = float(sum(o.kind in ("failed", "partial") for o in reviews))
    ran: dict[str, int] = defaultdict(int)
    for o in outs:
        ran[o.gate] += o.kind in RAN
    if ran:
        got["gate_reruns"] = float(sum(max(n - 1, 0) for n in ran.values()))
    return got


def _item_samples(span: Span, outs: list[Outcome], runs: list[tuple[Outcome, float]], ctx: Context):
    """stage -> the one figure this item gives it (absent when the log cannot say)."""
    return {
        **_waits(span, ctx),
        **_first_gate(span, outs, ctx),
        **_gates(runs),
        **_merge(span, outs, ctx),
        **_rework(outs),
    }


def _summarise(rows: list[dict], minimum: int) -> dict:
    stages: dict[str, dict] = {}
    for name in STAGES:
        vals = [r[name] for r in rows if name in r]
        stages[name] = stat(vals, minimum, unit=UNITS.get(name, "s"))
    return stages


def _gate_rows(by_gate: dict[str, list[tuple[Outcome, float]]], minimum: int) -> list[dict]:
    out = []
    for gate, runs in sorted(by_gate.items()):
        waits = [o.waited_s for o, _ in runs if o.waited_s is not None]
        run_s = [d - min(o.waited_s or 0.0, d) for o, d in runs]
        out.append({"gate": gate, "run": stat(run_s, minimum), "wait": stat(waits, minimum)})
    return out


def _gather(ctx: Context, group_by: str):
    """(group -> each item's stage samples, group -> gate -> runs, the slowest item's id)."""
    runs_of: dict[str, list[tuple[Outcome, float]]] = defaultdict(list)
    for o, secs in gate_durations(ctx):
        runs_of[o.item].append((o, secs))
    outs_of: dict[str, list[Outcome]] = defaultdict(list)
    for o in ctx.facts.outcomes:
        outs_of[o.item].append(o)
    members: dict[str, list[dict]] = defaultdict(list)
    gates: dict[str, dict[str, list[tuple[Outcome, float]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    slowest: tuple[float, str] = (-1.0, "")
    for span in ctx.facts.spans.values():
        if span.first_claim is None or not ctx.in_scope(span.item, span.first_claim):
            continue
        outs = sorted(outs_of.get(span.item, ()), key=lambda o: o.at)
        lines = max((o.changed_lines or 0 for o in outs), default=0)
        group = _group_of(group_by, span, lines, ctx)
        got = _item_samples(span, outs, runs_of.get(span.item, []), ctx)
        members[group].append(got)
        for o, d in runs_of.get(span.item, ()):
            gates[group][o.gate].append((o, d))
        total = got.get("claim_to_merge", span.cycle_s)
        if total is not None and total > slowest[0]:
            slowest = (total, span.item)
    return members, gates, slowest[1]


def build(ctx: Context, group_by: str = "") -> dict:
    """The stage split as plain data; ValueError on a group-by it does not know."""
    if group_by and group_by not in GROUPS:
        raise ValueError(f"unknown group-by {group_by!r}: one of {', '.join(GROUPS)}")
    minimum = int(ctx.cfg.metrics.min_samples)
    members, gates, slowest = _gather(ctx, group_by)
    groups = [
        {
            "group": g,
            "items": len(rows),
            "stages": _summarise(rows, minimum),
            "gates": _gate_rows(gates[g], minimum),
        }
        for g, rows in sorted(members.items())
    ]
    unavailable = {}
    for name in STAGES:
        n = sum(1 for rows in members.values() for r in rows if name in r)
        if n < max(minimum, 1):
            unavailable[name] = f"{n} items with a sample, need {minimum}"
    cli, mcp = f"ddflow metrics retro {slowest}", f"ddflow_metrics view=retro item={slowest}"
    if not slowest:  # no item gave a claim-to-merge: name a call that runs as written
        cli, mcp = (
            "ddflow metrics stages --group-by phase",
            "ddflow_metrics view=stages group_by=phase",
        )
    return {
        "group_by": group_by,
        "groups": groups,
        "not_measured": dict(NOT_MEASURED),
        "unavailable": unavailable,
        "next": {
            "cli": cli,
            "mcp": mcp,
            "note": (
                "the slowest item's timeline names its biggest gap"
                if slowest
                else "no item has a claim-to-merge yet; regroup to see where the figures are"
            )
            + "; groups: phase, kind, size, agent",
        },
    }


def has_figures(data: dict) -> bool:
    return any(
        s.get("p50_s") is not None
        or s.get("p50_rounds") is not None
        or s.get("p50_runs") is not None
        for g in data["groups"]
        for s in g["stages"].values()
    )


def _cell(s: dict, unit: str) -> str:
    v = s.get(f"p50_{unit}")
    if v is None:
        return f"n/a (n={s['n']})"
    p90 = s.get(f"p90_{unit}")
    return f"p50 {v:.0f}{unit if unit == 's' else ''} p90 {p90:.0f}{unit if unit == 's' else ''} (n={s['n']})"


def text(data: dict) -> str:
    """The human form, shared by CLI and MCP."""
    lines = ["cycle-time split (queue wait, lease wait, implement, gates, review, merge)"]
    for g in data["groups"]:
        lines.append(f"\n{g['group']}: {g['items']} item(s)")
        for name, s in g["stages"].items():
            lines.append(f"  {name:15} {_cell(s, UNITS.get(name, 's'))}")
        for r in g["gates"]:
            lines.append(
                f"  gate {r['gate']:12} run {_cell(r['run'], 's')}; wait {_cell(r['wait'], 's')}"
            )
    if not data["groups"]:
        lines.append("\nno item was claimed in this window")
    for name, why in data["not_measured"].items():
        lines.append(f"not measured: {name}: {why}")
    nxt = data["next"]
    lines.append(f"next: {nxt['cli']}  |  {nxt['mcp']}  ({nxt['note']})")
    return "\n".join(lines)
