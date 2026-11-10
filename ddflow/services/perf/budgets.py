"""Time budgets and the shrink-only ratchet (PM-budgets).

A budget is a knob in ``[perf_metrics]`` AND a recorded floor that only ever tightens (like
the file-size baseline: it is never raised without a recorded reason, and a proposed raise
without one is refused, never silently accepted). Three classes are budgeted: gate wall time
(p90 seconds), one test file's serial time, and one stage wait (lease wait, merge wait).

A series whose p90 exceeds its budget is a ``Breach`` -- a finding; when the severity knob is
``block`` it is a refusal that names the exact next call (see the slowest tests, split the
file, raise with a recorded reason), in CLI and MCP form (r-tell-dont-discover). Below the
sample floor the check returns ``None`` (never a zero, never a false clear); a measurement
that could not run is unavailable, never passed.

Pure functions only: no I/O, no scheduler. The store owns the samples; this module answers
"is this over budget" and "is this floor move allowed" from the values the caller already
has.
"""

from __future__ import annotations

from dataclasses import dataclass

from ddflow.config import Config
from ddflow.config_sections.perf_metrics import PerfMetricsConfig

#: The declared budgets: (budget name, the ``[perf_metrics]`` knob that holds its seconds).
#: ``stage:lease`` and ``stage:merge`` are one ``stage`` kind (the wait for a lease, the wait
#: for a merge); ``gate`` is a gate's wall time; ``test_file`` is one test file's serial time.
BUDGETS = (
    ("gate", "budget_gate_p90_s"),
    ("test_file", "budget_test_file_s"),
    ("stage:lease", "budget_stage_lease_wait_s"),
    ("stage:merge", "budget_stage_merge_wait_s"),
)


def _section_knobs(config: Config) -> PerfMetricsConfig:
    """Return ``config.perf_metrics``, spelling each budget knob once.

    The default ``PerfMetricsConfig`` read elsewhere in this module is spelled
    ``getattr(cfg, attr)`` (the attr is the knob name, invisible to the dead-knob linter,
    which counts only a section-qualified read spelled ``.perf_metrics.<knob>``). Naming each
    here -- the one place a full ``Config`` is in hand -- keeps the shrink-only linter honest
    (store.py's ``_section_knobs`` does the same for the ring/detect knobs).
    """
    section = config.perf_metrics
    _ = (
        config.perf_metrics.budget_gate_p90_s,
        config.perf_metrics.budget_test_file_s,
        config.perf_metrics.budget_stage_lease_wait_s,
        config.perf_metrics.budget_stage_merge_wait_s,
        config.perf_metrics.budget_breach_min_samples,
        config.perf_metrics.budget_severity,
    )
    return section


def kind_of(name: str) -> str:
    """The budget class of a budget name (``stage:lease`` -> ``stage``)."""
    for bname, _attr in BUDGETS:
        if bname == name:
            return bname.split(":", 1)[0]
    raise KeyError(name)


def budget_seconds(name: str, cfg: PerfMetricsConfig | None = None) -> float:
    """The knob value (seconds) of one declared budget; a name outside ``BUDGETS`` is refused."""
    cfg = cfg or _section_knobs(Config())
    for bname, attr in BUDGETS:
        if bname == name:
            return float(getattr(cfg, attr))
    raise KeyError(name)


def _p90(values: list[float], minimum: int) -> float | None:
    """The p90 by linear interpolation between the two bracketing ranks (R-7 style); None
    below ``minimum`` samples (a figure that could not be measured, never a 0)."""
    if len(values) < max(minimum, 1):
        return None
    ordered = sorted(values)
    idx = (len(ordered) - 1) * 0.9
    lo = int(idx)
    hi = min(lo + 1, len(ordered) - 1)
    frac = idx - lo
    return ordered[lo] + (ordered[hi] - ordered[lo]) * frac


#: The exact next call each budget class names when a breach blocks (a test file whose serial
#: time is over budget splits; a gate over budget asks which tests keep it there; a stage wait
#: over budget is raised only with a recorded reason).
_NEXT_CALLS = {
    "gate": "see the slowest tests that keep the gate over budget: `ddflow tests --full`"
    " (MCP: ddflow_tests full=\"true\")",
    "test_file": "split the slowest file by cohesion into a package that keeps the old"
    " imports; list the candidates: `ddflow tests --full` (MCP: ddflow_tests full=\"true\")",
    "stage": "raise the budget only with a recorded reason: `ddflow decision add`"
    " (MCP: ddflow_decision action=add)",
}


@dataclass(frozen=True)
class Breach:
    """One budget exceeded: the measured p90, the budget, how many samples, the severity."""

    budget: str  # the budget name ("gate", "test_file", "stage:lease", "stage:merge")
    kind: str  # "gate" | "test_file" | "stage"
    p90_s: float
    budget_s: float
    n: int
    severity: str  # "warn" (a finding) or "block" (a finding AND a refusal)

    @property
    def blocks(self) -> bool:
        return self.severity == "block"

    def next_call(self) -> str:
        """The next call this breach names, CLI and MCP form (r-tell-dont-discover)."""
        return _NEXT_CALLS[self.kind]


def check(
    name: str,
    values: list[float],
    cfg: PerfMetricsConfig | None = None,
) -> Breach | None:
    """A breach when the series' p90 exceeds its budget on at least the sample floor; None
    below the floor (not enough data) and None under budget (not a finding)."""
    cfg = cfg or _section_knobs(Config())
    p90 = _p90(values, cfg.budget_breach_min_samples)
    if p90 is None:
        return None
    budget_s = budget_seconds(name, cfg)
    if p90 <= budget_s:
        return None
    return Breach(name, kind_of(name), p90, budget_s, len(values), cfg.budget_severity)


def checks(
    measured: dict[str, list[float]],
    cfg: PerfMetricsConfig | None = None,
) -> list[Breach]:
    """Every declared budget whose (measured) p90 exceeds it, one Breach each. ``measured``
    maps a budget NAME to its duration samples; a name not in it is simply not checked."""
    cfg = cfg or _section_knobs(Config())
    out: list[Breach] = []
    for name, values in measured.items():
        b = check(name, list(values), cfg)
        if b is not None:
            out.append(b)
    return out


def ratchet(current_s: float, proposed_s: float, reason: str = "") -> tuple[float, str]:
    """The shrink-only floor move. A proposal at or below the current floor tightens (accepts
    and returns it); a proposal above the floor is refused without a recorded reason and
    accepted only when one is given. Returns (accepted floor seconds, note)."""
    if proposed_s <= current_s:
        return proposed_s, "tightened" if proposed_s < current_s else "unchanged"
    if reason:
        return proposed_s, f"raised with recorded reason: {reason}"
    return current_s, "refused: raising a budget floor needs a recorded reason"


__all__ = ["BUDGETS", "Breach", "budget_seconds", "check", "checks", "kind_of", "ratchet"]
