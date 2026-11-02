"""``ddflow metrics locks``: what the leases cost, and whether lighter locking is safe.

Repeats the 2026-11-02 measurements (R-lock-landscape-verified) from the facts the log
already gives (``facts.collect``; the log is not folded a second way). Git is asked only for
what the log does not hold, through ``lockgit.Probes``:

* ``hold``       per finished lease: claim to first gate, in gates, in reviews, last review to
                 release, merge to release (``after_merge``), and the whole hold;
* ``waits``      claim waits (first `lease.waiting` to the claim), the leases that blocked them,
                 and how many of those were false serialization: the waiter and every blocker
                 actually changed disjoint files (needs git);
* ``declared``   how much of a lease's globs was never touched, how much of what was written
                 lay outside them, and the items that wrote undeclared files (needs git);
* ``merges``     textual conflicts of recent merges replayed with merge-tree (needs git) and
                 red-main: merges whose item got a bug within ``[metrics] lock_red_main_window_s``;
* ``falsifiers`` conflicts or red-main above their knobs: tripped, naming the switch back.

A figure the log or git cannot give is None and named under ``unavailable``, never a zero.
Suggestion accept and answer times need events ddflow does not record yet: ``not_measured``.
"""

from __future__ import annotations

from ...core import globs as G
from ...core import progress as PR
from .facts import Landing, Lease
from .lockgit import Probes
from .providers import Context
from .stats import rate, stat
from .summary import gate_durations

HOLD_STAGES = (
    "claim_to_first_gate",
    "in_gates",
    "in_reviews",
    "last_review_to_release",
    "after_merge",
    "hold",
)
CHECKS = ("hold", "waits", "false_serialization", "declared", "conflict_rate", "red_main_rate")
NOT_MEASURED = {
    "suggestion_accept_rate": "ddflow records no suggestion events yet; LK-sg-events adds them",
    "suggestion_answer_time": "ddflow records no suggestion events yet; LK-sg-events adds them",
    "ci_red_after_apply": "ddflow records no suggestion events yet; LK-sg-events adds them",
}


def _union(spans: list[tuple[float, float]]) -> float:
    total, edge = 0.0, None
    for a, b in sorted(spans):
        if edge is None or a > edge:
            total, edge = total + (b - a), b
        elif b > edge:
            total, edge = total + (b - edge), b
    return total


def _runs(ctx: Context) -> dict[str, list[tuple[str, float, float]]]:
    """item -> (gate, start, end) of every timed gate run."""
    out: dict[str, list[tuple[str, float, float]]] = {}
    for o, secs in gate_durations(ctx):
        out.setdefault(o.item, []).append((o.gate, o.at - secs, o.at))
    return out


def _hold_row(lease: Lease, runs: list[tuple[str, float, float]], landings: list[Landing]):
    """One finished lease cut into its parts (a part the log cannot give is absent)."""
    a, b = lease.start, lease.end
    got: dict[str, float] = {"hold": b - a}
    inside = [(g, max(s, a), min(e, b)) for g, s, e in runs if e >= a and s <= b]
    if inside:
        got["claim_to_first_gate"] = min(s for _, s, _ in inside) - a
        review = [(s, e) for g, s, e in inside if g in PR.REVIEW_GATES]
        other = [(s, e) for g, s, e in inside if g not in PR.REVIEW_GATES]
        got["in_reviews"] = _union(review)
        got["in_gates"] = max(_union(review + other) - got["in_reviews"], 0.0)
        if review:
            got["last_review_to_release"] = b - max(e for _, e in review)
    merged = min((m.at for m in landings if a <= m.at <= b), default=None)
    if merged is not None:
        got["after_merge"] = b - merged
    return got


def _hold(ctx: Context, runs, minimum: int) -> dict:
    rows = []
    for lease in ctx.facts.leases:
        if lease.end is None or lease.end < lease.start or not ctx.in_scope(lease.item, lease.end):
            continue
        rows.append(
            _hold_row(lease, runs.get(lease.item, ()), ctx.facts.landings.get(lease.item, []))
        )
    stages = {n: stat([r[n] for r in rows if n in r], minimum) for n in HOLD_STAGES}
    merged = [r for r in rows if "after_merge" in r]
    share = sum(r["after_merge"] for r in merged) / max(sum(r["hold"] for r in merged), 1e-9)
    return {
        "leases": len(rows),
        "stages": stages,
        "after_merge_share": share if len(merged) >= max(minimum, 1) else None,
    }


def _declared_of(ctx: Context, item: str, at: float | None = None) -> tuple[str, ...]:
    """The globs of the item's lease that was held at ``at`` (its first lease when ``at`` is
    None or no lease spans it): a re-claim may declare other globs."""
    mine = [lease for lease in ctx.facts.leases if lease.item == item]
    if at is not None:
        held = [
            lease for lease in mine if lease.start <= at and (lease.end is None or at <= lease.end)
        ]
        mine = held or mine
    return mine[0].globs if mine else ()


def _landing_files(probes: Probes | None, m: Landing, cache: dict) -> list[str] | None:
    """Files one merge changed; None when git (or the log) cannot say."""
    if probes is None or not (m.before and m.after):
        return None
    if (m.before, m.after) not in cache:
        cache[(m.before, m.after)] = probes.files(m.before, m.after)
    return cache[(m.before, m.after)]


def _actual(
    ctx: Context, probes: Probes | None, item: str, cache: dict, lease: Lease | None = None
) -> frozenset | None:
    """Files the item's merges changed (those inside ``lease`` when given); None when git (or
    the log) cannot say."""
    lands = [
        m
        for m in ctx.facts.landings.get(item, ())
        if lease is None or (lease.start <= m.at and (lease.end is None or m.at <= lease.end))
    ]
    parts = [_landing_files(probes, m, cache) for m in lands]
    if not parts or any(p is None for p in parts):
        return None
    return frozenset(f for p in parts for f in p or ())


def _blockers(ctx: Context, item: str, since: float, mine: tuple[str, ...]) -> list[Lease]:
    """Other items holding a lease at ``since`` whose globs overlap the waiter's."""
    out = []
    for lease in ctx.facts.leases:
        live = lease.start <= since and (lease.end is None or lease.end > since)
        if live and lease.item != item and any(G.overlap(x, y) for x in mine for y in lease.globs):
            out.append(lease)
    return out


def _false_wait(
    ctx: Context, probes: Probes | None, item: str, began: float, claim: float, cache: dict
) -> bool | None:
    """Was this wait false serialization (waiter and every blocker wrote disjoint files)?
    None when it cannot be classified: no blocker found, or git could not say."""
    own = next((x for x in ctx.facts.leases if x.item == item and x.start == claim), None)
    blockers = _blockers(ctx, item, began, own.globs if own else _declared_of(ctx, item))
    mine = _actual(ctx, probes, item, cache, own)
    theirs = [_actual(ctx, probes, b.item, cache, b) for b in blockers]
    if not blockers or mine is None or any(t is None for t in theirs):
        return None
    return all(mine.isdisjoint(t or ()) for t in theirs)


def _waits(ctx: Context, probes: Probes | None, minimum: int, cache: dict) -> dict:
    spans, false_s, class_s, classified, false_n = [], 0.0, 0.0, 0, 0
    for item, queued in ctx.facts.waits.items():
        span = ctx.facts.spans.get(item)
        claim = span.first_claim if span else None
        before = [t for t in queued if claim is not None and t <= claim]
        if not before or not ctx.in_scope(item, claim):
            continue
        began, secs = min(before), claim - min(before)
        spans.append(secs)
        false = _false_wait(ctx, probes, item, began, claim, cache)
        if false is not None:
            classified, class_s = classified + 1, class_s + secs
            false_n, false_s = false_n + false, false_s + secs * false
    return {
        "spans": stat(spans, minimum),
        "total_s": sum(spans),
        "classified": classified,
        "false_serialization": {
            "spans": false_n,
            "seconds": false_s,
            "share_of_classified_seconds": false_s / class_s
            if class_s and classified >= max(minimum, 1)
            else None,
        },
    }


def _declared(ctx: Context, probes: Probes | None, minimum: int, cache: dict) -> dict:
    """Each merge inside the view is scored against the globs of the lease held at that merge."""
    untouched, outside, wrote, items = [], [], set(), set()
    for item, lands in ctx.facts.landings.items():
        for m in (m for m in lands if ctx.in_scope(item, m.at)):
            declared, actual = _declared_of(ctx, item, m.at), _landing_files(probes, m, cache)
            if not declared or actual is None:
                continue
            items.add(item)
            untouched.append(
                sum(not any(G.inside(f, g) for f in actual) for g in declared) / len(declared)
            )
            out = [f for f in actual if not any(G.inside(f, g) for g in declared)]
            if actual:
                outside.append(len(out) / len(actual))
            if out:
                wrote.add(item)
    items, wrote = len(items), len(wrote)
    enough = items >= max(minimum, 1)
    mean = lambda xs: sum(xs) / len(xs) if xs and enough else None  # noqa: E731
    return {
        "items": items,
        "declared_untouched_share": mean(untouched),
        "actual_outside_declared_share": mean(outside),
        "items_with_undeclared_writes": wrote if enough else None,
    }


def _merges(ctx: Context, probes: Probes | None, minimum: int) -> dict:
    lands = sorted(
        ((i, m) for i, ms in ctx.facts.landings.items() for m in ms if ctx.in_scope(i, m.at)),
        key=lambda p: p[1].at,
    )
    window = ctx.cfg.metrics.lock_red_main_window_s
    red = sum(
        any(i == b and m.at <= t <= m.at + window for b, t in ctx.facts.bugs) for i, m in lands
    )
    cap = max(int(ctx.cfg.metrics.lock_probe_max), 0)
    probed = [m for _, m in lands[-cap:]] if cap and probes is not None else []
    verdicts = [probes.conflicts(m.before, m.head) for m in probed] if probes else []
    known = [v for v in verdicts if v is not None]
    return {
        "merges": len(lands),
        "red_main": red,
        "red_main_rate": rate(red, len(lands), minimum),
        "probed": len(probed),
        "probe_unavailable": len(verdicts) - len(known),
        "conflicts": sum(known),
        "conflict_rate": rate(sum(known), len(known), minimum),
    }


def _falsifiers(ctx: Context, merges: dict) -> dict:
    m = ctx.cfg.metrics
    tests = (
        ("textual_conflicts", merges["conflict_rate"], m.lock_falsifier_conflict_pct),
        ("red_main", merges["red_main_rate"], m.lock_falsifier_red_main_pct),
    )
    rows = {
        name: {
            "rate": r,
            "limit_pct": limit,
            "state": "unknown" if r is None else "tripped" if r * 100 > limit else "holds",
        }
        for name, r, limit in tests
    }
    key, value = m.lock_revert_key, m.lock_revert_value
    tripped = [n for n, row in rows.items() if row["state"] == "tripped"]
    return {
        "rows": rows,
        "tripped": tripped,
        "revert": {
            "cli": f"ddflow config --set {key} {value}",
            "mcp": f"ddflow_configure set={key} value={value}",
        },
    }


def build(ctx: Context, probes: Probes | None = None) -> dict:
    """The lock report as plain data. ``probes`` None: the git figures are unavailable."""
    minimum, cache = int(ctx.cfg.metrics.min_samples), {}
    merges = _merges(ctx, probes, minimum)
    data = {
        "hold": _hold(ctx, _runs(ctx), minimum),
        "waits": _waits(ctx, probes, minimum, cache),
        "declared": _declared(ctx, probes, minimum, cache),
        "merges": merges,
        "falsifiers": _falsifiers(ctx, merges),
        "not_measured": dict(NOT_MEASURED),
    }
    data["unavailable"] = _unavailable(data, probes is not None)
    return data


def _unavailable(data: dict, git: bool) -> dict:
    why = (
        {}
        if git
        else {"git": "no git access: file, conflict and false-serialization figures need it"}
    )
    checks = {
        "hold": data["hold"]["stages"]["hold"]["p50_s"],
        "waits": data["waits"]["spans"]["p50_s"],
        "false_serialization": data["waits"]["false_serialization"]["share_of_classified_seconds"],
        "declared": data["declared"]["declared_untouched_share"],
        "conflict_rate": data["merges"]["conflict_rate"],
        "red_main_rate": data["merges"]["red_main_rate"],
    }
    for name, value in checks.items():
        if value is None:
            why[name] = "too few samples (or git could not tell)"
    return why


def has_figures(data: dict) -> bool:
    return any(k in CHECKS and k not in data["unavailable"] for k in CHECKS)


def _pct(v) -> str:
    return "n/a" if v is None else f"{v * 100:.1f}%"


def _stat(s: dict) -> str:
    if s["p50_s"] is None:
        return f"n/a (n={s['n']})"
    return f"p50 {s['p50_s']:.0f}s p90 {s['p90_s']:.0f}s (n={s['n']})"


def text(data: dict) -> str:
    """The human form, shared by CLI and MCP."""
    h, w, d, m, f = (data[k] for k in ("hold", "waits", "declared", "merges", "falsifiers"))
    out = [f"lease hold split ({h['leases']} finished lease(s))"]
    out += [f"  {n:24} {_stat(s)}" for n, s in h["stages"].items()]
    out.append(f"  after-merge share of hold {_pct(h['after_merge_share'])}")
    fs = w["false_serialization"]
    out += [
        f"waits: {_stat(w['spans'])}; {w['classified']} classified, "
        f"{fs['spans']} false ({_pct(fs['share_of_classified_seconds'])} of classified wait time)",
        f"declared globs ({d['items']} merged item(s)): untouched {_pct(d['declared_untouched_share'])}, "
        f"written outside {_pct(d['actual_outside_declared_share'])}, "
        f"items with undeclared writes {d['items_with_undeclared_writes'] if d['items_with_undeclared_writes'] is not None else 'n/a'}",
        f"merges: {m['merges']}; textual conflicts {m['conflicts']}/{m['probed'] - m['probe_unavailable']} "
        f"probed ({_pct(m['conflict_rate'])}, {m['probe_unavailable']} unavailable); "
        f"red-main {m['red_main']} ({_pct(m['red_main_rate'])})",
    ]
    for name, row in f["rows"].items():
        out.append(
            f"falsifier {name}: {row['state']} (limit {row['limit_pct']}%, now {_pct(row['rate'])})"
        )
    if f["tripped"]:
        out.append(
            f"TRIPPED ({', '.join(f['tripped'])}): switch the locking back to strict: "
            f"{f['revert']['cli']}  |  {f['revert']['mcp']}"
        )
    out += [f"not measured: {k}: {v}" for k, v in data["not_measured"].items()]
    out += [f"unavailable: {k}: {v}" for k, v in data["unavailable"].items()]
    out.append("next: ddflow metrics locks --since 7d  |  ddflow_metrics view=locks since=7d")
    return "\n".join(out)
