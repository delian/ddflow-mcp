"""The swarm baseline: what a pilot lane fleet must beat, read from the event log.

Read-only and pure: ``baseline`` folds the events once (``metrics.facts.collect``, the
collector ``ddflow metrics`` uses; there is no second one) and answers over a window:

* ``throughput``: merged items per hour (first claim to last completion of the window);
* ``rework``: items returned from a gate (a failed gate before they completed) or with a
  bug filed after they completed within ``bug_window_s``, over merged items;
* ``cost``: dollars per merged item when events carry ``cost_usd`` (none do today, so it
  is None and says so), and always the proxy ``agent_hours_per_item`` (lease-held time
  plus recorded review time) so a before/after pair compares like with like;
* ``critical_path``: the longest dependency chain of the window's merged items weighted by
  their observed cycle time, the phase wall time, and the slack between them.

A figure under ``[metrics] min_samples`` merged items is None, never a zero. The same call
with ``since``/``until`` gives the "before" and "after" of a lane-consolidation or pilot
comparison; ``compare`` turns two results into the ratios the swarm targets are stated in.
``view`` is the ``swarm`` view of ``ddflow metrics`` and ``provider`` the headline rows of its
summary; ``metrics.service`` resolves both when first used.
"""

from __future__ import annotations

from collections.abc import Sequence

from ..config import Config
from ..core import progress as PR
from ..core.events import Event
from ..core.model import State
from ..core.schedule import critical_path, inherited_deps
from .metrics.facts import Facts, Span, collect
from .metrics.providers import Context
from .metrics.service import Answer
from .metrics.summary import RAN
from .swarm.ledger import BUG_WINDOW_S

HOUR = 3600.0

#: Swarm targets (D-swarm-answers Q4): throughput multiple, cost multiple, rework points.
TARGET_THROUGHPUT_X = 1.8
TARGET_COST_X = 1.3
TARGET_REWORK_POINTS = 5.0

#: Why an agent-hours ratio is not judged against TARGET_COST_X (D-swarm-pilot-2026-11-05).
COST_NOT_MEASURED = (
    "the 1.3x cost target is stated in dollars; this is agent-hours, not dollars, "
    "so the target is not measured"
)
UNAVAILABLE = "n/a"


def _merged(ctx: Context, until: float | None) -> list[Span]:
    """Work items (not phases) first completed inside the window."""
    return [
        s
        for s in ctx.facts.spans.values()
        if s.kind != "phase"
        and s.completed_at is not None
        and ctx.in_scope(s.item, s.completed_at)
        and (until is None or s.completed_at <= until)
    ]


def _held_s(facts: Facts, items: set[str], now: float) -> float:
    """Lease-held seconds of ``items`` (agent time, overlapping leases both counted)."""
    return sum(
        max((ls.end if ls.end is not None else now) - ls.start, 0.0)
        for ls in facts.leases
        if ls.item in items
    )


def _review_s(facts: Facts, items: set[str]) -> float:
    return sum(
        o.elapsed_s or 0.0
        for o in facts.outcomes
        if o.item in items and o.kind in RAN and o.elapsed_s
    )


def _cost_usd(events: Sequence[Event], items: set[str]) -> tuple[float, int]:
    """(sum of ``cost_usd`` recorded on events of ``items``, events that carried one)."""
    total, n = 0.0, 0
    for ev in events:
        d = ev.data if isinstance(ev.data, dict) else {}
        ev_d = d.get("evidence") if isinstance(d.get("evidence"), dict) else {}
        v = d.get("cost_usd", ev_d.get("cost_usd"))
        if (
            ev.subject in items
            and isinstance(v, int | float)
            and not isinstance(v, bool)
            and v >= 0
        ):
            total, n = total + float(v), n + 1
    return total, n


def _rework(ctx: Context, merged: list[Span], window_s: float) -> dict:
    done = {s.item: s.completed_at or 0.0 for s in merged}
    bugged = {
        item for item, at in ctx.facts.bugs if item in done and 0 < at - done[item] <= window_s
    }
    gate_returned = {
        o.item
        for o in ctx.facts.outcomes
        if o.kind == "failed" and o.item in done and o.at <= done[o.item]
    }
    review_returned = {
        o.item
        for o in ctx.facts.outcomes
        if o.kind == "failed"
        and o.gate in PR.REVIEW_GATES
        and o.item in done
        and o.at <= done[o.item]
    }
    hit = gate_returned | bugged
    n = len(merged)
    return {
        "merged": n,
        "returned_from_gate": len(gate_returned),
        "returned_from_review": len(review_returned),
        "bug_after_merge": len(bugged),
        "review_rework_rate": len(review_returned | bugged) / n if n else None,
        "reworked": len(hit),
        "rate": len(hit) / n if n else None,
    }


def _chain(ctx: Context, merged: list[Span]) -> tuple[list[str], float]:
    """Longest weighted chain among ``merged`` (weight = first claim to completion)."""
    state = ctx.facts.state
    by_id = {s.item: s for s in merged}
    weight = {i: (s.cycle_s or 0.0) for i, s in by_id.items()}
    deps: dict[str, list[str]] = {i: [] for i in by_id}
    if state is not None:
        for i in by_id:
            it = state.items.get(i)
            if it is not None:
                deps[i] = sorted({d for _o, d in inherited_deps(state, it, ctx.cfg) if d in by_id})
    best: dict[str, tuple[float, list[str]]] = {}
    busy: set[str] = set()  # an unexpected cycle contributes nothing rather than looping

    def walk(i: str) -> tuple[float, list[str]]:
        if i in best:
            return best[i]
        if i in busy:
            return 0.0, []
        busy.add(i)
        pre = max((walk(d) for d in deps[i]), key=lambda p: p[0], default=(0.0, []))
        busy.discard(i)
        best[i] = (pre[0] + weight[i], [*pre[1], i])
        return best[i]

    top = max((walk(i) for i in by_id), key=lambda p: p[0], default=(0.0, []))
    return top[1], top[0]


def _wall(merged: list[Span]) -> tuple[float | None, float | None, float | None]:
    """(start, end, wall seconds): first claim to last completion of the merged items."""
    claimed = [s.first_claim for s in merged if s.first_claim is not None]
    start = min(claimed) if claimed else None
    end = max((s.completed_at or 0.0) for s in merged) if merged else None
    ok = start is not None and end is not None and end > start
    return start, end, (end - start if ok else None)  # type: ignore[operator]


def _cost(events: Sequence[Event], facts: Facts, items: set[str], now: float, minimum: int) -> dict:
    n = len(items)
    agent_h = (_held_s(facts, items, now) + _review_s(facts, items)) / HOUR
    usd, usd_events = _cost_usd(events, items)
    return {
        "usd_total": usd if usd_events else None,
        "usd_per_item": usd / n if usd_events and n >= minimum else None,
        "agent_hours_per_item": agent_h / n if agent_h and n >= minimum else None,
        "events_with_cost": usd_events,
    }


def _path(ctx: Context, merged: list[Span], wall: float | None, phase: str, thin: bool) -> dict:
    open_length = len(
        critical_path(ctx.facts.state, phase, ctx.cfg)
    )  # of unfinished work: no sample rule
    if thin:  # the merged chain rests on too few items to be a figure
        return dict.fromkeys(
            ("merged_chain", "length", "duration_s", "wall_s", "slack_s", "parallelism")
        ) | {"open_length": open_length}
    chain, chain_s = _chain(ctx, merged)
    cycles = sum(s.cycle_s or 0.0 for s in merged)
    return {
        "merged_chain": chain,
        "length": len(chain),
        "duration_s": chain_s if chain else None,
        "wall_s": wall,
        "slack_s": wall - chain_s if wall is not None and chain else None,
        "parallelism": cycles / wall if wall else None,
        "open_length": open_length,
    }


def baseline(
    events: Sequence[Event],
    state: State,
    cfg: Config,
    now: float,
    *,
    phase: str = "",
    since: float | None = None,
    until: float | None = None,
    bug_window_s: float = BUG_WINDOW_S,
) -> dict:
    """The baseline figures over the window as plain data (see the module doc)."""
    facts = collect(events, state, now, cfg)
    ctx = Context(facts, cfg, now, since, phase)
    merged = _merged(ctx, until)
    minimum = max(int(cfg.metrics.min_samples), 1)
    n = len(merged)
    thin = n < minimum
    why = f"{n} merged items, need {minimum}"
    start, end, wall = _wall(merged)
    rw = _rework(ctx, merged, bug_window_s)
    cost = _cost(events, facts, {s.item for s in merged}, now, minimum)
    per_hour = n / (wall / HOUR) if wall and not thin else None
    unavailable = {
        k: v
        for k, v in {
            "throughput": why if thin else "" if per_hour else "no elapsed time in the window",
            "rework": why if thin else "",
            "cost": why if thin else "",
            "cost_usd": ""
            if cost["events_with_cost"]
            else "no event carries cost_usd; agent_hours_per_item is the proxy",
        }.items()
        if v
    }
    if thin:
        rw["rate"] = rw["review_rework_rate"] = None
    return {
        "window": {"phase": phase, "since": since, "until": until, "start": start, "end": end},
        "merged": n,
        "throughput": {"per_hour": per_hour, "wall_s": wall},
        "rework": rw,
        "cost": cost,
        "critical_path": _path(ctx, merged, wall, phase, thin),
        "unavailable": unavailable,
    }


def _ratio(after: float | None, before: float | None) -> float | None:
    return after / before if after is not None and before else None


def compare(before: dict, after: dict) -> dict:
    """Ratios of two ``baseline`` results against the swarm targets (None: not derivable).

    ``cost_x`` falls back to the agent-hours proxy so a pair still compares like with like, but
    only dollars judge the cost target: on the proxy ``targets["cost"]`` stays None and
    ``not_measured`` says why."""
    thr = _ratio(after["throughput"]["per_hour"], before["throughput"]["per_hour"])
    usd = _ratio(after["cost"]["usd_per_item"], before["cost"]["usd_per_item"])
    proxy = _ratio(after["cost"]["agent_hours_per_item"], before["cost"]["agent_hours_per_item"])
    cost_x = usd if usd is not None else proxy
    r0, r1 = before["rework"]["rate"], after["rework"]["rate"]
    points = (r1 - r0) * 100 if r0 is not None and r1 is not None else None

    return {
        "throughput_x": thr,
        "cost_x": cost_x,
        "cost_basis": "usd" if usd is not None else "agent_hours" if proxy is not None else None,
        "rework_points": points,
        "targets": {
            "throughput": None if thr is None else thr >= TARGET_THROUGHPUT_X,
            "cost": None if usd is None else usd <= TARGET_COST_X,
            "rework": None if points is None else points <= TARGET_REWORK_POINTS,
        },
        "not_measured": {} if usd is not None else {"cost": COST_NOT_MEASURED},
    }


def _figure(value: float | None, unit: str = "", digits: int = 2) -> str:
    return UNAVAILABLE if value is None else f"{value:.{digits}f}{unit}"


def text(data: dict) -> str:
    """The human form of a ``baseline`` result: ``ddflow metrics swarm``."""
    rate, cp = data["rework"]["rate"], data["critical_path"]
    out = [
        f"swarm baseline: {data['merged']} merged item(s)",
        f"  merged items per hour   {_figure(data['throughput']['per_hour'])}",
        f"  rework rate             {_figure(None if rate is None else rate * 100, '%')}",
        f"  agent-hours per item    {_figure(data['cost']['agent_hours_per_item'], digits=4)}",
        f"  dollars per item        {_figure(data['cost']['usd_per_item'])}",
        f"  critical path           {cp['length'] or UNAVAILABLE} item(s), "
        f"slack {_figure(cp['slack_s'], 's')}, parallelism {_figure(cp['parallelism'])}",
    ]
    if data["unavailable"]:
        out += ["", "unavailable (not zero):"]
        out += [f"  {k}: {why}" for k, why in sorted(data["unavailable"].items())]
    return "\n".join(out)


def provider(ctx: Context) -> list[dict]:
    """The ``metrics.register`` provider: the baseline's headline rows for ``ddflow metrics``."""
    merged = _merged(ctx, None)
    n = len(merged)
    items = {s.item for s in merged}
    rw = _rework(ctx, merged, BUG_WINDOW_S)
    wall = _wall(merged)[2]
    agent_s = _held_s(ctx.facts, items, ctx.now) + _review_s(ctx.facts, items)
    return [
        {
            "metric": "swarm.merged_per_hour",
            "group": "",
            "n": n,
            "value": n / (wall / HOUR) if wall else None,
        },
        {"metric": "swarm.rework_rate", "group": "", "n": n, "value": rw["rate"]},
        {
            "metric": "swarm.agent_hours_per_item",
            "group": "",
            "n": n,
            "value": agent_s / HOUR / n if agent_s and n else None,  # no lease: unmeasured, not 0
        },
    ]


def view(
    events: Sequence[Event], state: State, cfg: Config, now: float, phase: str, since: float | None
) -> Answer:
    """``ddflow metrics swarm``: the baseline, exit 2 while it rests on too few merged items."""
    data = baseline(events, state, cfg, now, phase=phase, since=since)
    ok = data["throughput"]["per_hour"] is not None
    why = f"not enough data: {data['unavailable'].get('throughput', '')}"
    return Answer(ok, "" if ok else why, metrics=data, text=text(data))
