"""Proposal generators for the TEST SUITE (PM-prop-tests).

From the test-duration rollup (:mod:`ddflow.services.perf.testtimes`), the file sizes the package
owns (the ``[quality].max_file_bytes`` size rule) and the impact-selection evidence ci already
records (``ci_scope``), these propose -- never apply -- test-suite changes, in the same shape
:mod:`ddflow.services.perf.prop_flow` uses for the workflow:

* a SPLIT for a slow file whose share of the critical-path floor exceeds ``prop_split_share``
  and whose size is over the size rule (the SZ split pattern then splits it);
* a SHARD or a WORKER change: shard the slowest file when the suite is skewed (that file
  dominates the floor at or above ``prop_shard_skew``); raise the xdist worker count when it is
  NOT skewed (the workers divide the work, so more of them still lowers the floor);
* a WIDENED IMPACT SELECTION when the selected fraction ci records leaves a measured number of
  failures unrun across ``prop_select_min_runs`` runs (a static import-graph selector only; a
  learned selector is out of scope until there is labelled history). Flaky-test QUARANTINE is
  already proposed by :mod:`ddflow.services.perf.flakyrate` (PM-flaky); this module does not
  duplicate it.

Propose-only and pure: no I/O, no scheduler, no store; the store owns the samples these read.
No prompt text, arguments, user paths or secrets appear in any figure.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ddflow.config import Config
from ddflow.config_sections.perf_metrics import PerfMetricsConfig


def _section_knobs(config: Config) -> PerfMetricsConfig:
    """Return ``config.perf_metrics``, spelling every proposal knob once (the dead-knob
    linter counts only a section-qualified read as ``.perf_metrics.<knob>``)."""
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
    """One proposed change. ``advice`` is surfaced to the operator -- it never mutates a
    suite, a gate or a lane."""

    kind: str  # "split_file" | "shard" | "workers" | "selection"
    advice: Any
    evidence: dict
    because: str

    def next_call(self) -> str:
        """The operator's exact next call to act on this proposal, CLI and MCP form
        (r-tell-dont-discover). These PROPOSE only; recording the change is a person's act."""
        return _NEXT_CALL


#: The one next call for a propose-only measure, CLI then MCP (r-tell-dont-discover). None of
#: these APPLY the change: recording the decision is a person's act.
_NEXT_CALL = (
    "propose only: record the change `ddflow decision add` (MCP: ddflow_decision action=add)"
)


def propose_split_files(
    rollup: dict,
    *,
    sizes: dict[str, int] | None = None,
    max_file_bytes: int = 0,
    cfg: PerfMetricsConfig | None = None,
) -> list[Proposal]:
    """A split candidate per file whose share of the critical-path floor is at least
    ``prop_split_share`` and whose size (bytes) is over ``max_file_bytes`` (the size rule).
    ``rollup`` is ``testtimes.rollup``'s dict; ``sizes`` maps a file id to its byte size, and
    files with no entry stay whole. ``max_file_bytes`` 0 means no size is read."""
    cfg = cfg or _section_knobs(Config())
    share = cfg.prop_split_share
    floor = float(rollup.get("critical_path_floor_s") or 0.0)
    out: list[Proposal] = []
    if floor <= 0:
        return out
    for file, secs in sorted((rollup.get("per_file") or {}).items()):
        if share > 0 and secs / floor < share:
            continue
        size = (sizes or {}).get(file)
        if max_file_bytes > 0 and (size is None or size <= max_file_bytes):
            continue  # under the size rule, or no size in hand: not a split candidate
        out.append(
            Proposal(
                "split_file",
                {"file": file, "serial_s": secs, "floor_s": floor, "size_bytes": size},
                {"share": secs / floor, "files": len(rollup.get("per_file") or {})},
                f"{file} is {secs:.1f}s of a floor {floor:.1f}s: split it, it dominates the suite",
            )
        )
    return out


def propose_shard_or_workers(
    rollup: dict,
    *,
    cfg: PerfMetricsConfig | None = None,
) -> Proposal | None:
    """SHARD the slowest file when the suite is skewed (that file dominates the floor at or
    above ``prop_shard_skew``); RAISE the worker count when it is not (the workers divide the
    work, so raising them still lowers the serial-over-workers floor). A floor of 0 has no
    slowest file and no proposal."""
    cfg = cfg or _section_knobs(Config())
    floor = float(rollup.get("critical_path_floor_s") or 0.0)
    skew = float(rollup.get("skew") or 0.0)
    per_file = rollup.get("per_file") or {}
    if floor <= 0 or not per_file:
        return None
    slowest = max(per_file, key=lambda f: per_file[f])
    if skew >= cfg.prop_shard_skew:
        return Proposal(
            "shard",
            {"kind": "shard", "file": slowest},
            {"skew": skew, "floors": floor, "file_s": per_file[slowest], "workers": rollup.get("workers")},
            f"{slowest} is {skew:.2f} of the floor: shard it by historical duration",
        )
    return Proposal(
        "workers",
        {"kind": "raise", "workers": rollup.get("workers")},
        {"skew": skew, "floor_s": floor, "serial_s": rollup.get("serial_seconds")},
        "the suite is not skewed: raise the worker count, which still lowers the floor",
    )


def propose_selection(
    runs: list[dict[str, Any]],
    *,
    cfg: PerfMetricsConfig | None = None,
) -> Proposal | None:
    """WIDEN the impact selection when ``runs`` records at least ``prop_select_miss_min``
    failures a selection left unrun across at least ``prop_select_min_runs`` runs (a miss is a
    failure the periodic full run found that the selection would not have run). One run's
    "misses" is ``runs[i]["misses"]`` and its selected fraction ``runs[i]["selected_fraction"]``;
    a missing count is 0, not a measured miss."""
    cfg = cfg or _section_knobs(Config())
    if len(runs) < cfg.prop_select_min_runs:
        return None  # one run's misses rest on too few runs below the floor: not-enough-data
    in_horizon = [r for r in runs if int(r.get("misses") or 0) > 0]
    misses = sum(int(r.get("misses") or 0) for r in in_horizon)
    if misses < cfg.prop_select_miss_min:
        return None
    fraction = [r.get("selected_fraction") for r in runs if r.get("selected_fraction") is not None]
    return Proposal(
        "selection",
        "widen",
        {
            "runs": len(runs),
            "misses": misses,
            "selected_fraction": (sorted(fraction) or None),
        },
        f"{misses} missed failure(s) across {len(runs)} runs: widen the selection",
    )


__all__ = [
    "Proposal",
    "propose_selection",
    "propose_shard_or_workers",
    "propose_split_files",
]
