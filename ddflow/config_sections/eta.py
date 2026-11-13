"""``[eta]``: how the developer-E TA figures are computed.

Kept separate from the ``metrics`` knobs because ETA is a prediction layer on top of the
same log: its window and sample floor are dialled independently of the descriptive
statistics (the two are answers to different questions). The coverage bar is the falsifier
of R-eta-prediction, made a knob so a stale model is demoted to "low confidence" rather
than trusted silently.
"""

from __future__ import annotations

from dataclasses import dataclass

from ._docs import declare, knob


def _bar(value: object) -> str:
    return (
        ""
        if isinstance(value, (int, float)) and not isinstance(value, bool) and 0.0 < value < 1.0
        else "a coverage fraction strictly between 0 and 1"
    )


@declare("eta")
@dataclass
class EtaConfig:
    """Developer-facing time-to-finish estimates: the window the log is read back over, and
    the honesty floor under which the estimate is 'insufficient data' instead of a number."""

    window_days: float = knob(
        30.0,
        doc="How many days the derived duration tables look back over, measured at each item's completion. Completions outside it supply no sample; shorten it to read the recent trend, widen it to smooth a thin log.",
    )
    min_samples: int = knob(
        10,
        doc="Fewest samples a derived ETA figure may rest on. Under it every figure is 'insufficient data' (never a number, the sample count is named), so a prediction never rests on noise.",
    )
    coverage_bar: float = knob(
        0.80,
        doc="The fraction of true completions the 10-90 interval must cover for the estimate to be trusted (R-eta-prediction's shipping bar, operator 2026-10-31). Below it the range is still shown, labelled low confidence.",
        check=_bar,
    )
