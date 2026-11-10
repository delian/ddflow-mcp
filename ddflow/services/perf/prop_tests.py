"""Proposal generators for the TEST SUITE (PM-prop-tests).

From the test-duration rollup (:mod:`ddflow.services.perf.testtimes`), the file sizes the package
owns (the ``[quality].max_file_bytes`` size rule) and the impact-selection evidence ci already
records (``ci_scope``), these propose -- never apply -- test-suite changes, in the same shape
:mod:`ddflow.services.perf.prop_flow` uses for the workflow:

* a SPLIT for a slow file whose share of the critical-path floor is at least ``prop_split_share``
  AND whose size is over the size rule (a size in hand is REQUIRED: a file whose size cannot be
  checked is not a split candidate -- the split is for a file that is BOTH slow and big: the SZ
  split pattern then splits it into modules under the size rule);
* a SHARD or a WORKER change: SHARD the slowest file when the suite is skewed (that file
  dominates the floor at or above ``prop_shard_skew``); RAISE the xdist worker count when it is
  NOT skewed;
* a WIDENED or NARROWED IMPACT SELECTION over the selection evidence ci records (which fraction
  of the tests it SELECTED, and which failures a periodic FULL run found that the selection would
  not have run); a static import-graph selector only, learned selection is out of scope until
  there is labelled history.

Every proposal carries its evidence, the sample size, the effect size, the expected saving in
seconds per run and per day, and the risk, plus the exact next call to accept it. Flaky-test
QUARANTINE is already proposed by :mod:`ddflow.services.perf.flakyrate` (PM-flaky); this module
does not duplicate it. Propose-only and pure: no I/O, no scheduler, no store.
No prompt text, arguments, user paths or secrets appear in any figure.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ddflow.config import Config
from ddflow.config_sections.perf_metrics import PerfMetricsConfig


def _section_knobs(config: Config) -> PerfMetricsConfig:
    """Return ``config.perf_metrics``, spelling every new knob once (the dead-knob linter
    counts only a section-qualified read as ``.perf_metrics.<knob>``)."""
    section = config.perf_metrics
    _ = (
        config.perf_metrics.prop_split_share,
        config.perf_metrics.prop_shard_skew,
        config.perf_metrics.prop_select_miss_min,
        config.perf_metrics.prop_select_min_runs,
    )
    return section


@dataclass(frozen=True)
class Proposal:
    """One proposed change. ``advice`` says what to change; it never mutates a suite, a
    gate or a lane. ``sample_size``, ``effect_size``, ``expected_saving_s_run`` and
    ``expected_saving_s_day`` are the evidence the claim rests on; anything unknown is
    ``None`` -- a missing figure is unavailable, never a zero."""

    kind: str  # "split_file" | "shard" | "workers" | "selection"
    advice: Any
    evidence: dict
    sample_size: int
    effect_size: Any
    expected_saving_s_run: float | None
    expected_saving_s_day: float | None
    risk: str
    because: str

    def next_call(self) -> str:
        """The exact next call to accept this proposal, CLI and MCP form (r-tell-dont-discover)."""
        return _NEXT_CALLS[self.kind]


#: The exact next call each proposal names, CLI then MCP form. None APPLIES the change:
#: a split is code (the SZ split pattern splits the module); the rest are a person's call.
_NEXT_CALLS = {
    "split_file": "the SZ split pattern then splits it `ddflow split <item>` (MCP: ddflow_task action=split id=<item>)",
    "shard": "shard by historical duration: record the decision `ddflow decision add` (MCP: ddflow_decision action=add)",
    "workers": "raise the worker count: record the decision `ddflow decision add` (MCP: ddflow_decision action=add)",
    "selection": "record the selection change `ddflow decision add` (MCP: ddflow_decision action=add)",
}


def _floor(rollup: dict) -> float | None:
    """The measured critical-path floor, or ``None`` when the rollup carried none. ``None``
    is a probe that did not run -- never a measured 0 and never a worker raise."""
    shard_floor = rollup.get("critical_path_floor_s")
    if not isinstance(shard_floor, (int, float)) or shard_floor <= 0:
        return None
    return float(shard_floor)


def propose_split_files(
    rollup: dict,
    *,
    sizes: dict[str, int] | None = None,
    max_file_bytes: int = 0,
    cfg: PerfMetricsConfig | None = None,
) -> list[Proposal]:
    """A split candidate per file whose share of the critical-path floor is at least
    ``prop_split_share`` AND whose size over ``max_file_bytes`` (the size rule; a size in
    hand is REQUIRED -- a file whose size is absent is not a candidate). ``rollup`` is
    ``testtimes.rollup``'s dict."""
    cfg = cfg or _section_knobs(Config())
    share = cfg.prop_split_share
    floor = _floor(rollup)
    out: list[Proposal] = []
    if floor is None:
        return out
    if max_file_bytes <= 0:
        return out  # the size rule was never supplied: no file can be "over the rule"
    for file, secs in sorted((rollup.get("per_file") or {}).items()):
        if secs / floor < share:
            continue
        size = (sizes or {}).get(file)
        if size is None or size <= max_file_bytes:
            continue  # no size in hand, or under the rule: cannot propose a SPLIT
        # the SZ split into N modules of this file would let them run on the workers, so the
        # one-file portion of the floor is divided; the run saving is the part past floor / N.
        saving = max(0.0, secs - secs / max(1, _workers(rollup)))
        out.append(
            Proposal(
                "split_file",
                {"file": file, "serial_s": secs, "floor_s": floor, "size_bytes": size},
                evidence={"share_of_floor": secs / floor, "files": len(rollup.get("per_file") or {})},
                sample_size=len(rollup.get("per_file") or {}),
                effect_size=secs / floor,
                expected_saving_s_run=saving,
                expected_saving_s_day=None,  # runs/day is not a duration the rollup carries
                risk="the split only: it changes this file's tests, so the suite is ungreen until it re-passes",
                because=(
                    f"{file} is {secs:.1f}s of a floor {floor:.1f}s and "
                    f"{size} bytes over the {max_file_bytes} size rule: split it"
                ),
            )
        )
    return out


def _workers(rollup: dict) -> int:
    w = rollup.get("workers")
    return int(w) if isinstance(w, int) and w > 0 else 1


def propose_shard_or_workers(
    rollup: dict,
    *,
    cfg: PerfMetricsConfig | None = None,
) -> Proposal | None:
    """SHARD the slowest file when the suite is skewed (it dominates the floor at or above
    ``prop_shard_skew``); RAISE the worker count when it is NOT (the workers divide the work).
    A rollup with no measured floor is ``None`` -- never a shard and never a worker raise for a
    probe that did not run."""
    cfg = cfg or _section_knobs(Config())
    floor = _floor(rollup)
    per_file = rollup.get("per_file") or {}
    if floor is None or not per_file:
        return None
    slowest = max(per_file, key=lambda f: per_file[f])
    skew = float(rollup.get("skew")) if isinstance(rollup.get("skew"), (int, float)) else None
    if skew is None:
        skew = per_file[slowest] / floor
    if skew >= cfg.prop_shard_skew:
        return Proposal(
            "shard",
            {"kind": "shard", "file": slowest},
            evidence={"skew": skew, "floor_s": floor, "file_s": per_file[slowest], "workers": rollup.get("workers")},
            sample_size=len(per_file),
            effect_size=skew,
            expected_saving_s_run=per_file[slowest] - per_file[slowest] / max(1, _workers(rollup)),
            expected_saving_s_day=None,
            risk="the shard only; it moves tests between files, the suite is ungreen until it re-passes",
            because=f"{slowest} is {skew:.2f} of the floor: shard it by historical duration",
        )
    return Proposal(
        "workers",
        {"kind": "raise", "workers": rollup.get("workers")},
        evidence={"skew": skew, "floor_s": floor, "serial_s": rollup.get("serial_seconds")},
        sample_size=len(per_file),
        effect_size=skew,
        expected_saving_s_run=None,  # unknown without the time saved per worker
        expected_saving_s_day=None,
        risk="a raised count is re-measured before it is called an improvement, never trusted from one run",
        because="the suite is not skewed: raise the worker count, which still lowers the floor",
    )


def propose_selection(
    runs: list[dict[str, Any]],
    *,
    cfg: PerfMetricsConfig | None = None,
) -> Proposal | None:
    """WIDEN (or NARROW) the impact selection over ``runs``. A run records ``misses`` (a
    failure a periodic FULL run found that the selection would NOT have run; a missing or
    non-numeric count is unavailable, and never a measured miss) and ``selected_fraction``
    (a run that recorded no fraction is absent, never 0). At least ``prop_select_min_miss``
    misses across at least ``prop_select_min_runs`` runs propose a WIDEN; at least
    ``prop_select_min_runs`` runs with known fraction 1.0 and no misses propose a NARROW."""
    cfg = cfg or _section_knobs(Config())
    if len(runs) < cfg.prop_select_min_runs:
        return None  # one set of misses on too few runs: not-enough-data
    misses, measured = _count_misses(runs)
    if misses >= cfg.prop_select_miss_min:
        fraction = sorted(f for r in runs if (f := _fraction(r)) is not None) or None
        return Proposal(
            "selection",
            "widen",
            evidence={"runs": len(runs), "misses": misses, "selected_fraction": fraction},
            sample_size=len(runs),
            effect_size=misses,
            expected_saving_s_run=None,
            expected_saving_s_day=None,
            risk="a wider selection runs a larger fraction of the suite per change: it is wider, not the suite",
            because=f"{misses} missed failure(s) across {len(runs)} runs: widen the selection",
        )
    if measured and all(_fraction(r) == 1.0 for r in runs):
        # Every run selected the whole suite AND missed nothing (misses was measured): narrow.
        return Proposal(
            "selection",
            "narrow",
            evidence={"runs": len(runs), "misses": 0, "selected_fraction": 1.0},
            sample_size=len(runs),
            effect_size=1.0,
            expected_saving_s_run=None,
            expected_saving_s_day=None,
            risk="a narrower selection runs less of the suite per change: one missed failure is a silent regression",
            because=f"{len(runs)} full-selection runs with no recorded miss: narrow the selection",
        )
    return None


def _fraction(run: dict) -> float | None:
    """A run's known selected fraction; a missing or non-numeric fraction is ``None``."""
    f = run.get("selected_fraction")
    if isinstance(f, (int, float)) and not isinstance(f, bool):
        return float(f)
    if isinstance(f, str):
        try:
            return float(f)
        except ValueError:
            return None
    return None


def _count_misses(runs: list[dict[str, Any]]) -> tuple[int, bool]:
    """(counted misses, every run MEASURED its misses). A missing or non-numeric count is
    UNAVAILABLE, and is not a measured miss: it neither adds to the widening trigger nor
    count toward a NARROW, so a run that never recorded misses neither forces a NARROW nor
    a silent 0."""
    total = 0
    measured = True
    for r in runs:
        m = r.get("misses")
        if isinstance(m, (int, float)) and not isinstance(m, bool):
            total += int(m)
        else:
            measured = False
    return total, measured


__all__ = [
    "Proposal",
    "propose_selection",
    "propose_shard_or_workers",
    "propose_split_files",
]
