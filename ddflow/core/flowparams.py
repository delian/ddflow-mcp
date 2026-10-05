"""The adaptive controller's parameters, read from `[schedule]`.

`core/flowcontrol` is stdlib only and knows nothing of configuration; this is the one
place that turns the operator's knobs into its :class:`~ddflow.core.flowcontrol.Params`,
so every caller (the sampler, `plan`, the operator surface) folds with the same numbers.
Pure: a function of the loaded :class:`~ddflow.config.Config`.
"""

from __future__ import annotations

from ..config import Config
from . import flowcontrol as FC


def enabled_signals(cfg: Config) -> tuple[str, ...]:
    """The signals `[schedule.signals].enabled` lets the controller read, in order."""
    return tuple(cfg.schedule.signals.get("enabled") or ())


def thresholds(cfg: Config) -> dict[str, FC.Threshold]:
    """One :class:`~ddflow.core.flowcontrol.Threshold` per ENABLED signal with marks.

    A disabled signal has none, so it can neither lower nor justify raising the limit.
    """
    sig = cfg.schedule.signals
    out: dict[str, FC.Threshold] = {}
    for name in enabled_signals(cfg):
        marks = sig.get(name)
        if not isinstance(marks, dict) or "low" not in marks or "high" not in marks:
            continue
        out[name] = FC.Threshold(
            low=float(marks["low"]),
            high=float(marks["high"]),
            critical=None if marks.get("critical") is None else float(marks["critical"]),
        )
    return out


def params(cfg: Config) -> FC.Params:
    """`[schedule]` as the controller reads it. `max_parallel_tasks` is the start value;
    no increase follows a decrease for twice `adapt_up_after_s` (D-adaptive-flow-accepted)."""
    s = cfg.schedule
    enabled = set(enabled_signals(cfg))
    return FC.Params(
        start=s.max_parallel_tasks,
        floor=s.max_parallel_min,
        ceiling=s.max_parallel_max,
        adapt_up_after_s=s.adapt_up_after_s,
        cooldown_s=s.adapt_cooldown_s,
        quiet_after_decrease_s=2 * s.adapt_up_after_s,
        sample_every_s=s.signal_interval_s,
        thresholds=thresholds(cfg),
        host_signals=tuple(h for h in FC.HOST_SIGNALS if h in enabled),
    )
