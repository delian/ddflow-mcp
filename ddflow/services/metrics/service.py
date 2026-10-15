"""One entry point for every metrics view: ``run`` returns what to say and how it went."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date

from ...config import Config
from ...core import clock
from ...core.events import Event
from ...core.model import State
from . import facts as F
from . import forecast as FC
from . import grid, render, retro, skips, summary
from .providers import Context

VIEWS = ("summary", "forecast", "retro", "skips", "lanes")


@dataclass
class Answer:
    ok: bool  # figures were computed
    reason: str = ""  # when not ok: what is missing (exit 2) or wrong (failed=True)
    failed: bool = False
    metrics: dict = field(default_factory=dict)
    text: str = ""


def parse_since(text: str, now: float) -> float | None:
    """``--since`` as epoch seconds: a duration back from ``now`` (7d, 12h) or a date."""
    text = (text or "").strip()
    if not text:
        return None
    try:
        return now - clock.parse_duration(text)
    except ValueError:
        pass
    try:
        day = clock.parse_date(text)
    except ValueError:
        raise ValueError(
            f"--since {text!r}: use a duration such as 7d or 12h, or a date YYYY-MM-DD"
        ) from None
    return clock.epoch(f"{day.isoformat()}T00:00:00Z") if isinstance(day, date) else None


def run(
    events: Sequence[Event],
    state: State,
    cfg: Config,
    now: float,
    *,
    view: str = "summary",
    item: str = "",
    phase: str = "",
    since: str = "",
    group_by: str = "",
) -> Answer:
    """``now`` is an argument, not a read of the clock: a test freezes it."""
    if view not in VIEWS:
        return Answer(False, f"unknown view {view!r}: one of {', '.join(VIEWS)}", failed=True)
    try:
        floor = parse_since(since, now)
    except ValueError as exc:
        return Answer(False, str(exc), failed=True)
    if phase and phase not in state.items:
        return Answer(False, f"no phase {phase!r}", failed=True)
    facts = F.collect(events, state, now)
    ctx = Context(facts, cfg, now, floor, phase)
    if view == "forecast":
        f = FC.forecast(facts, state, cfg, now, phase)
        return Answer(f["available"], f["reason"], metrics=f, text=render.forecast(f))
    if view == "retro":
        if not item:
            return Answer(False, "retro needs an item id", failed=True)
        if item not in state.items:
            return Answer(False, f"no item {item!r}", failed=True)
        t = retro.timeline(events, facts, item)
        reason = "" if t["biggest_gap"] else f"{item} has fewer than two events to compare"
        return Answer(not reason, reason, metrics=t, text=render.retro(t))
    if view == "skips":
        a = skips.audit(ctx)
        return Answer(True, metrics=a, text=render.skips(a))
    if view == "lanes":
        g = grid.sample(events, cfg, now, floor)
        u = grid.utilisation(g["rows"])
        reason = "" if u["utilisation"] is not None else "no lane worked in the sampled window"
        return Answer(not reason, reason, metrics={**g, **u}, text=render.lanes(g, u))
    try:
        data = summary.build(ctx, group_by or "gate")
    except ValueError as exc:
        return Answer(False, str(exc), failed=True)
    ok = summary.has_figures(data)
    reason = (
        ""
        if ok
        else f"not enough data: fewer than {cfg.metrics.min_samples} samples behind any figure"
    )
    return Answer(ok, reason, metrics=data, text=render.summary(data))
