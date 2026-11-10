"""The lane-wait metrics report, surfaced through the reporting package (`status`, `doctor`).

Read-only: it folds the log and returns the figures plus their text. The computation lives in
``services.lanewait`` (pure); this module only wires it to a fold and renders the text.
"""

from __future__ import annotations

from ...adapters.clock import DEFAULT as CLOCK
from ...services import lanewait as LW
from ...services.metrics.render import dur


def lane_wait_metrics(log, st, cfg, now=None, events=None) -> dict:
    """The lane-wait figures as of ``now``, with ``text`` and the plain ``data`` keys beside."""
    now = CLOCK.wall() if now is None else now
    data = LW.lane_wait(log.read_all() if events is None else events, st, cfg, now)
    return {**data, "text": _text(data)}


def _text(data: dict) -> str:
    gw = data["gate_wait_hours"]
    idle = data["idle"]
    rtc = data["ready_to_claimed"]
    ci = data["ci_lock_queue"]
    out = [
        "lane wait (derived from the event log)",
        f"  ready to claimed: {_stat(rtc)}",
        f"  lane-hours queued for shared gates: {dur(gw['total'] * 3600.0)}",
        "  ci lock queue: " + _stat(ci),
        "  lane utilisation: "
        f"{_pct(idle['utilisation'])} over {idle['samples']} instants, working lane-samples "
        f"{idle['working_lane_samples']}, idle beside ready work "
        f"{idle['idle_with_ready_lane_samples']}",
        "  merged: "
        f"{data['merged']['merges']} in the window"
        + (
            f", {data['merged']['per_hour']:.3f}/hour"
            if data["merged"]["per_hour"] is not None
            else ""
        ),
    ]
    if data["unavailable"]:
        out.append("  unavailable (not zero):")
        out.extend(f"    {k}: {why}" for k, why in sorted(data["unavailable"].items()))
    return "\n".join(out)


def _stat(s: dict) -> str:
    return f"n={s['n']} p50 {dur(s['p50_s'])} p90 {dur(s['p90_s'])}"


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.0f}%"
