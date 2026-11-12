"""One entry point for every metrics view: ``run`` returns what to say and how it went."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from ...adapters.registry import Registry
from ...config import Config
from ...core import clock
from ...core.events import Event
from ...core.metrics import VIEWS
from ...core.model import State
from .batch import build as batch_build
from .batch import has_figures as batch_ok
from .batch import text as batch_text
from .facts import collect
from .forecast import forecast as forecast_view
from .grid import sample as grid_sample
from .grid import utilisation
from .lockgit import Probes
from .locks import build as locks_build
from .locks import has_figures as locks_ok
from .locks import text as locks_text
from .path_variants import build as variants_build
from .path_variants import has_figures as variants_ok
from .path_variants import text as variants_text
from .paths import build as paths_build
from .paths import has_figures as paths_ok
from .paths import text as paths_text
from .providers import Context, register
from .render import forecast as text_forecast
from .render import lanes as text_lanes
from .render import retro as text_retro
from .render import skips as text_skips
from .render import summary as text_summary
from .retro import timeline
from .runs_to_merge import provider as runs_provider
from .skips import audit
from .slo import assess as slo_assess
from .slo import ok as slo_ok
from .slo import text as slo_text
from .stages import build as stages_build
from .stages import has_figures as stages_ok
from .stages import text as stages_text
from .summary import build, has_figures
from .tiny import build as tiny_build
from .tiny import has_figures as tiny_ok
from .tiny import text as tiny_text

#: `swarm_metrics` and the swarm ledger import this package, so these resolve when first used.
SWARM: Registry[Callable[..., Any]] = Registry("swarm metrics")
SWARM.lazy("view", "ddflow.services.swarm_metrics:view")
SWARM.lazy("rows", "ddflow.services.swarm_metrics:provider")


def _swarm_rows(ctx: Context) -> Any:
    return SWARM["rows"](ctx)


register("swarm", _swarm_rows)
register("runs", runs_provider)


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


def _stages(ctx: Context, group_by: str) -> Answer:
    return _figure(ctx, lambda c: stages_build(c, group_by), stages_ok, stages_text, "stage")


def _figure(ctx: Context, build, ok_of, text_of, subject: str) -> Answer:
    """The shared figure a derived view answers: build once (a ``ValueError`` is a bad
    call, not a figure), one min-sample gate, one text. Every derived view routes through
    here so the repeated body is not a duplicate-code cluster."""
    try:
        data = build(ctx)
    except ValueError as exc:
        return Answer(False, str(exc), failed=True)
    ok = ok_of(data)
    why = f"not enough data: fewer than {ctx.cfg.metrics.min_samples} samples behind any {subject}"
    return Answer(ok, "" if ok else why, metrics=data, text=text_of(data))


def _locks(ctx: Context, probes: Probes | None) -> Answer:
    return _figure(ctx, lambda c: locks_build(c, probes), locks_ok, locks_text, "lock figure")


def _tiny(ctx: Context) -> Answer:
    return _figure(ctx, tiny_build, tiny_ok, tiny_text, "figure")


def _paths(ctx: Context) -> Answer:
    return _figure(ctx, paths_build, paths_ok, paths_text, "figure")


def _variants(ctx: Context) -> Answer:
    return _figure(ctx, variants_build, variants_ok, variants_text, "figure")


def _retro(item: str, events: Sequence[Event], facts: Any, state: State) -> Answer:
    """The ``retro`` view: one item's timeline, refused without an id or a known item."""
    if not item:
        return Answer(False, "retro needs an item id", failed=True)
    if item not in state.items:
        return Answer(False, f"no item {item!r}", failed=True)
    t = timeline(events, facts, item)
    reason = "" if t["biggest_gap"] else f"{item} has fewer than two events to compare"
    return Answer(not reason, reason, metrics=t, text=text_retro(t))


def _lanes(events: Sequence[Event], cfg: Config, now: float, floor: float | None) -> Answer:
    """The ``lanes`` view: a grid sample over who worked in the window, with utilisation."""
    g = grid_sample(events, cfg, now, floor)
    u = utilisation(g["rows"])
    reason = "" if u["utilisation"] is not None else "no lane worked in the sampled window"
    return Answer(not reason, reason, metrics={**g, **u}, text=text_lanes(g, u))


def run(  # noqa: C901, PLR0911 -- one return per view in a dispatch table, like ``Server.handle``
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
    probes: Probes | None = None,
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
    if view == "swarm":  # the baseline folds the log itself
        return SWARM["view"](events, state, cfg, now, phase, floor)
    facts = collect(events, state, now, cfg)
    ctx = Context(facts, cfg, now, floor, phase)
    if view == "forecast":
        f = forecast_view(facts, state, cfg, now, phase)
        return Answer(f["available"], f["reason"], metrics=f, text=text_forecast(f))
    if view == "retro":
        return _retro(item, events, facts, state)
    if view == "stages":
        return _stages(ctx, group_by)
    if view == "locks":
        return _locks(ctx, probes)
    if view == "tiny":
        return _tiny(ctx)
    if view == "paths":
        return _paths(ctx)
    if view == "variants":
        return _variants(ctx)
    if view == "slo":
        return _figure(ctx, slo_assess, slo_ok, slo_text, "service-level indicator")
    if view == "skips":
        a = audit(ctx)
        return Answer(True, metrics=a, text=text_skips(a))
    if view == "lanes":
        return _lanes(events, cfg, now, floor)
    if view == "batch":
        return _figure(ctx, batch_build, batch_ok, batch_text, "size bucket")
    try:
        data = build(ctx, group_by or "gate")
    except ValueError as exc:
        return Answer(False, str(exc), failed=True)
    ok = has_figures(data)
    reason = (
        ""
        if ok
        else f"not enough data: fewer than {cfg.metrics.min_samples} samples behind any figure"
    )
    return Answer(ok, reason, metrics=data, text=text_summary(data))
