"""The human text of every metrics view, built from the same data the JSON carries."""

from __future__ import annotations

from ...core import clock

UNAVAILABLE = "n/a"


def dur(seconds: float | None) -> str:
    """A length of time, or n/a: seconds under a minute, minutes under an hour, else hours."""
    if seconds is None:
        return UNAVAILABLE
    hour, minute = clock.DURATION_UNITS["h"], clock.DURATION_UNITS["m"]
    if seconds < minute:
        return f"{seconds:.0f}s"
    return f"{seconds / minute:.0f}m" if seconds < hour else f"{seconds / hour:.1f}h"


def pct(value: float | None) -> str:
    return UNAVAILABLE if value is None else f"{value * 100:.0f}%"


def _stat(label: str, s: dict, unit: str = "s", fmt=dur) -> str:
    return (
        f"  {label}: n={s['n']}  p50 {fmt(s[f'p50_{unit}'])}  p90 {fmt(s[f'p90_{unit}'])}"
        f"  max {fmt(s[f'max_{unit}'])}"
    )


def summary(data: dict) -> str:
    rounds = lambda v: UNAVAILABLE if v is None else f"{v:.1f}"  # noqa: E731
    out = [
        "metrics (derived from the event log)",
        _stat("cycle time (claim to done)", data["cycle_time"]),
        _stat("review rounds per item", data["review_rounds"], "rounds", rounds),
        _stat("queue wait (ready to claimed)", data["queue_wait"]),
        _stat("  of it, behind a glob conflict", data["lease_wait"]),
        "  rework: "
        f"{data['rework']['reworked']} of {data['rework']['completed']} completed items hit by a "
        f"later bug ({pct(data['rework']['rate'])})",
        "",
        "time per gate",
    ]
    out += [
        f"  {r['gate']:<14} n={r['n']:<5} p50 {dur(r['p50_s'])}  p90 {dur(r['p90_s'])}"
        for r in data["gate_time"]
    ]
    out += ["", f"outcomes by {data['group_by']}"]
    out += [
        f"  {r['group'][:34]:<34} n={r['n']:<5} fail {pct(r['failure_rate'])}  "
        f"skip {pct(r['skip_rate'])}  unavailable {pct(r['unavailable_rate'])}"
        for r in data["outcomes"]
    ]
    for r in data["providers"]:
        v = UNAVAILABLE if r.get("value") is None else r["value"]
        out.append(
            f"  [{r['provider']}] {r.get('group', '')} {r.get('metric', '')}: {v} (n={r.get('n')})"
        )
    if data["unavailable"]:
        out += ["", "unavailable (not zero):"]
        out += [f"  {k}: {why}" for k, why in sorted(data["unavailable"].items())]
    return "\n".join(out)


def forecast(f: dict) -> str:
    head = f"forecast{' for ' + f['phase'] if f['phase'] else ''} (an estimate, not a promise)"
    if not f["available"]:
        return f"{head}\n  {f['reason']}"
    return (
        f"{head}\n  critical path: {' > '.join(f['chain'])}\n"
        f"  P{f['low_pct']} {dur(f['low_s'])}   P{f['high_pct']} {dur(f['high_s'])} from now "
        f"({f['runs']} draws from {sum(f['samples'].values())} completed items: {f['samples']})\n"
        f"  {f['note']}"
    )


def retro(t: dict) -> str:
    if not t["entries"]:
        return f"{t['item']}: no events"
    out = [f"{t['item']}: {dur(t['total_s'])} from first to last event"]
    for r in t["entries"]:
        out.append(
            f"  +{dur(r.get('offset_s', 0.0)):>6}  {clock.fmt_minute(clock.iso_at(r['at']))}  {r['what']}"
        )
    big = t["biggest_gap"]
    if big:
        out.append(f"biggest gap: {dur(big['seconds'])} between '{big['from']}' and '{big['to']}'")
    return "\n".join(out)


def skips(a: dict) -> str:
    out = [f"gate skip audit: {a['total_skipped']} skip(s)"]
    for g in a["gates"]:
        out.append(
            f"  {g['gate']:<14} skipped {g['skipped']}  passed {g['passed']}  "
            f"skip rate {pct(g['skip_rate'])}  never passed after {g['never_passed_after']}"
        )
        out += [f"      {r['n']:>4}x {r['reason']}" for r in g["reasons"]]
    out.append("by agent:")
    out += [f"  {r['agent']}: {r['skipped']}" for r in a["by_agent"]]
    return "\n".join(out)


def lanes(g: dict, util: dict) -> str:
    cut = " (capped: older history not sampled)" if g["capped"] else ""
    out = [
        f"lane utilisation: {pct(util['utilisation'])} over {util['samples']} instants, "
        f"{g['step_s']} s apart{cut}"
    ]
    out.append(
        f"  working lane-samples {util['working']}, idle beside ready work {util['idle_with_ready']}"
    )
    return "\n".join(out)
