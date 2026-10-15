"""Plain-text output of the ledger and the advice: a table a terminal and a model both read."""

from __future__ import annotations

from collections.abc import Sequence

from ...core.clock import fmt_age
from .advise import Move
from .ledger import Lane
from .safe import safe

#: Seconds: below an hour a span reads in minutes, below two days in hours.
HOUR_S, TWO_DAYS_S = 3600, 172800


def _span(seconds: float) -> str:
    if seconds < HOUR_S:
        return fmt_age(seconds, "m")
    if seconds < TWO_DAYS_S:
        return fmt_age(seconds, "h")
    return fmt_age(seconds, "days")


def _row(ln: Lane, now: float) -> tuple[str, ...]:
    beat = _span(now - ln.heartbeat) + " ago" if ln.heartbeat else "-"
    held = _span(now - ln.since) if ln.since else "-"
    return (
        safe(ln.name),
        safe(ln.harness) or "-",
        safe(ln.model) or "-",
        safe(ln.item) or "-",
        beat,
        ln.state,
        held,
    )


def render_ledger(lanes: Sequence[Lane], now: float) -> str:
    """One line per lane: name, harness, model, item, last heartbeat, state, how long."""
    head = ("LANE", "HARNESS", "MODEL", "ITEM", "HEARTBEAT", "STATE", "FOR")
    rows = [head, *(_row(ln, now) for ln in lanes)]
    widths = [max(len(r[i]) for r in rows) for i in range(len(head))]
    lines = ["  ".join(c.ljust(w) for c, w in zip(r, widths, strict=True)).rstrip() for r in rows]
    for ln in lanes:
        if ln.detail and ln.state not in ("working",):
            lines.append(f"  {safe(ln.name)}: {safe(ln.detail)}")
        lines += [f"  {safe(ln.name)}: {safe(w)}" for w in ln.waits]
    return "\n".join(lines) if lanes else "no lanes active in the window"


def render_advice(moves: Sequence[Move]) -> str:
    if not moves:
        return "nothing to do: no lane needs a move"
    out = []
    for i, m in enumerate(moves, 1):
        out.append(f"{i}. [{m.kind}] {m.text}")
        if m.command:
            out.append(f"   run: {m.command}")
    return "\n".join(out)
