"""The `[schedule]` section: its dataclass, the values it accepts and its knob docs.

Re-exported from `ddflow.config`, which assembles `Config` from every section."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ._docs import _doc

#: Every signal the adaptive flow controller can read: the host ones
#: (`core/flowcontrol.HOST_SIGNALS`, sampled by `infra/signals`) and the log-derived ones
#: (`core/flowsignals`). Spelled out here because `config` imports nothing;
#: tests/test_adaptive_config.py holds the three lists together.
FLOW_SIGNALS: tuple[str, ...] = (
    "load_per_core",
    "memory_pressure",
    "disk_pressure",
    "reviewer_latency_ratio",
    "gate_failure_rate",
    "gate_failure_ratio",
    "merge_failure_rate",
    "loop_findings",
    "independent_ready",
)
#: The marks a signal may carry, higher always worse: at or under `low` healthy, over
#: `high` bad, at or over `critical` (optional) pauses admission.
SIGNAL_MARKS = ("low", "high", "critical")
#: The knob's dotted name.
SIGNALS = "schedule.signals"


#: The shipped marks (D-adaptive-flow-accepted for load; D-unify 8 for the rest). Memory and
#: disk are pressures, `1 - free fraction`: free memory under 15% shrinks the limit and
#: under 5% pauses admission; free disk under 10% shrinks and under 3% pauses. The two
#: ratios are to the project's own baseline: over 2x shrinks. Each `low` is where the
#: signal counts as healthy again (a hysteresis band below `high`), so a value hovering
#: at the mark neither grows nor shrinks the limit.
SHIPPED_MARKS: dict[str, dict[str, float]] = {
    "load_per_core": {"low": 0.15, "high": 0.75},
    "memory_pressure": {"low": 0.75, "high": 0.85, "critical": 0.95},
    "disk_pressure": {"low": 0.85, "high": 0.90, "critical": 0.97},
    "reviewer_latency_ratio": {"low": 1.5, "high": 2.0},
    "gate_failure_ratio": {"low": 1.5, "high": 2.0},
}


def default_signals() -> dict[str, Any]:
    """The shipped `[schedule.signals]`: every signal enabled, with `SHIPPED_MARKS`. A
    signal with no marks (gate_failure_rate, merge_failure_rate, loop_findings,
    independent_ready) is read but never moves the limit until marks are set for it."""
    return {"enabled": list(FLOW_SIGNALS), **{k: dict(v) for k, v in SHIPPED_MARKS.items()}}


def strictest_signals(base: dict[str, Any]) -> dict[str, Any]:
    """What a `[schedule.signals]` table that is not valid in a config FILE falls back to
    (D-enum-fallback-strict: a typo can only make ddflow more careful): every signal
    enabled, and per signal the lower of each mark from the layers below (`base`) and the
    shipped default. The bad table's own numbers are not trusted at all.

    Lower marks are stricter (higher is always worse), and the result is valid: taken over
    tables each valid on its own, the lowest low is under the lowest high, and the lowest
    critical is not under it."""
    out: dict[str, Any] = {"enabled": list(FLOW_SIGNALS)}
    shipped = default_signals()
    for name in FLOW_SIGNALS:
        # Only a table that is valid on its own takes part: marks mixed from a partial
        # table (a `high` with no `low`) could invert the band. The loader only ever
        # passes validated layers, so this hardens the helper for direct callers.
        tables = [
            t
            for t in (base.get(name), shipped.get(name))
            if isinstance(t, dict) and not _signals_problem({name: t})
        ]
        marks = {
            m: min(vals)
            for m in SIGNAL_MARKS
            if (vals := [t[m] for t in tables if _number(t.get(m))])
        }
        if "low" in marks and "high" in marks:
            out[name] = marks
    return out


def merge_signals(base: dict[str, Any], layer: dict[str, Any]) -> dict[str, Any]:
    """`layer` over `base`: `enabled` replaced whole, each signal's marks merged mark by
    mark, so a local `high` keeps the committed `low`."""
    out = {k: (dict(v) if isinstance(v, dict) else v) for k, v in base.items()}
    for key, value in layer.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = {**out[key], **value}
        else:
            out[key] = dict(value) if isinstance(value, dict) else value
    return out


def _number(v: Any) -> bool:
    return isinstance(v, int | float) and not isinstance(v, bool)


def _signals_problem(v: Any) -> str:
    known = ", ".join(FLOW_SIGNALS)
    if not isinstance(v, dict):
        return "must be a table: enabled = [...] and one table of marks per signal"
    enabled = v.get("enabled", [])
    if not isinstance(enabled, list) or not all(isinstance(x, str) for x in enabled):
        return f"enabled must be a list of signal names drawn from {known}"
    if bad := [x for x in enabled if x not in FLOW_SIGNALS]:
        return f"unknown signal {bad[0]!r} in enabled; the signals are {known}"
    for name, marks in v.items():
        if name == "enabled":
            continue
        if name not in FLOW_SIGNALS:
            return f"unknown signal {name!r}; the signals are {known}"
        if not isinstance(marks, dict):
            return f"{name} must be a table of marks ({', '.join(SIGNAL_MARKS)})"
        if bad := [m for m in marks if m not in SIGNAL_MARKS]:
            return f"{name}.{bad[0]} is not a mark; a signal has {', '.join(SIGNAL_MARKS)}"
        if bad := [m for m, x in marks.items() if not _number(x)]:
            return f"{name}.{bad[0]} must be a number"
        low, high, crit = (marks.get(m) for m in SIGNAL_MARKS)
        if low is None or high is None:
            return f"{name} needs both a low and a high mark"
        if low > high:
            return f"{name}: low ({low}) must not exceed high ({high})"
        if crit is not None and crit < high:
            return f"{name}: critical ({crit}) must not be under high ({high})"
    return ""


@dataclass
class ScheduleConfig:
    """Dependency resolution and parallel fan-out."""

    #: auto | fixed: whether the number of items in flight adapts (D-adaptive-flow-accepted)
    parallel: str = "auto"
    #: the START value in auto, the number itself in fixed
    max_parallel_tasks: int = 4
    max_parallel_min: int = 2
    max_parallel_max: int = 8
    adapt_up_after_s: int = 600
    adapt_cooldown_s: int = 300
    signal_interval_s: int = 60
    #: `[schedule.signals]`: `enabled` (the signals the controller reads) and, per
    #: signal, its `low` / `high` / `critical` marks. Merged layer by layer over the
    #: shipped default, so setting one mark keeps the others.
    signals: dict[str, Any] = field(default_factory=default_signals)
    ready_policy: str = "deps_and_lease"  # deps_and_lease | deps_only
    cycle_policy: str = "error"  # error | warn
    unknown_dep_policy: str = "block"  # block | warn
    empty_phase: str = "note"  # note | problem | off
    bugs_first: bool = True
    #: `name=capacity` for each physical resource items may declare.
    resources: list[str] = field(default_factory=list)
    #: `name=path` for sibling repositories whose items `needs` may name as `name:ID`.
    repos: list[str] = field(default_factory=list)


_doc(
    "schedule",
    "repos",
    'Sibling repositories a dependency may point into, as `name=path` (relative to this repository\'s root): ["run_nemo_run=../run_nemo_run"]. Then `needs = ["run_nemo_run:132.D"]` waits for item 132.D THERE to be done. `ddflow external sync` reads their logs and records what it observed in this one, so readiness is decided from a dated fact and never by reaching into another repository mid-decision. An unobserved external dependency is unmet.',
)
_doc(
    "schedule",
    "resources",
    'Capacities of the physical resources work may declare with `--resources`, as `name=capacity`: ["gpu=8", "vllm-fleet=1"]. A claim is refused (exit 3) when the live leases\' declared use of a resource plus its own would exceed the capacity -- counted across EVERY holder, because a GPU does not care which agent is using it. A resource named nowhere here has capacity 1: exclusive. Globs keep two agents out of one file; this keeps two agents from both starting an 8-GPU run on an 8-GPU box.',
)


_doc(
    "schedule",
    "empty_phase",
    "How to report an OPEN phase with no task under it — work in the queue that `ddflow next` can never offer. 'note' (default) mentions it, 'problem' fails `doctor`, 'off' stays silent. Configurable because a project that files phases before breaking them down lives in this state on purpose, while one that does not has found a planning gap. A phase whose tasks are all FINISHED while the phase stays open is always a problem and is not covered by this knob: it is not a workflow style, it is a queue held open by an item nobody can act on.",
)
_doc(
    "schedule",
    "bugs_first",
    "Offer bug fixes before features (default true). A task is a bug fix when it carries one of `[flow] bugfix_tags` or `hotfix_tags`, or an OPEN bug record names it (`ddflow bug found --item`). Priority still orders bugs among themselves and features among themselves, and the parallelism cap hands its slots to bugs first -- so a standing bug is fixed before more features are built on it. `false` orders by priority alone.",
)
_doc(
    "schedule",
    "max_parallel_tasks",
    "How many items may be in flight at once, counted across the whole queue. Every live lease counts, worktree or not -- a review task occupies an agent just as a coding task does. In `parallel = 'auto'` (the default) this is the START value the adaptive limit begins from and returns to; in 'fixed' it is the limit itself. Distinct from worktree.max_parallel, which caps only the leases that made a tree.",
)
_doc(
    "schedule",
    "parallel",
    "'auto' (default) or 'fixed'. 'auto': the number of items in flight adapts between `max_parallel_min` and `max_parallel_max`, starting at `max_parallel_tasks` -- raised by one after `adapt_up_after_s` of healthy samples while the limit was binding, lowered by a quarter when a signal is bad on 3 of the last 4 samples -- and the limit is derived on this machine, never committed. 'fixed': `max_parallel_tasks` is the number, exactly as before adaptive parallelism. A shrink never touches a running lease; it only stops new admissions.",
)
_doc(
    "schedule",
    "max_parallel_min",
    "The floor of the adaptive limit (default 2, at least 1): however bad the signals, auto never offers fewer slots than this. Must not exceed `max_parallel_tasks` in auto.",
)
_doc(
    "schedule",
    "max_parallel_max",
    "The ceiling of the adaptive limit (default 8, at least 1): auto never exceeds it, whatever the signals say. Must not be under `max_parallel_tasks` in auto. Machine sizing: set it in the local layer (`ddflow config --local --set schedule.max_parallel_max N`), which wins over the committed value.",
)
_doc(
    "schedule",
    "adapt_up_after_s",
    "Seconds of all-healthy samples, with the limit actually binding (in flight reached it), before auto raises the limit by one (default 600). After a decrease no increase happens for twice this long.",
)
_doc(
    "schedule",
    "adapt_cooldown_s",
    "Minimum seconds between two decreases of the adaptive limit (default 300), so one bad stretch lowers it once rather than to the floor.",
)
_doc(
    "schedule",
    "signal_interval_s",
    "Seconds between two samples of the signals auto steers by (default 60, at least 1). Samples are taken opportunistically by next, brief, claim and heartbeat -- there is no daemon -- and kept in a git-ignored ring under .ddflow/local/flow/.",
)
_doc(
    "schedule",
    "signals",
    "The `[schedule.signals]` table: `enabled`, the signals auto reads (default all: load_per_core, memory_pressure, disk_pressure, reviewer_latency_ratio, gate_failure_rate, gate_failure_ratio, merge_failure_rate, loop_findings, independent_ready), and per signal a table of marks, higher always worse: `low` (at or under it healthy), `high` (over it bad; between the two is the hysteresis band) and an optional `critical` (pauses admission for that evaluation). Shipped marks: load_per_core low 0.15, high 0.75; memory_pressure (1 - free memory) low 0.75, high 0.85, critical 0.95 -- free memory under 15% shrinks the limit, under 5% pauses admission; disk_pressure low 0.85, high 0.90, critical 0.97 -- free disk under 10% shrinks, under 3% pauses; reviewer_latency_ratio and gate_failure_ratio (each over the project's own 7-day baseline) low 1.5, high 2.0 -- over twice the usual shrinks. A signal with no marks (gate_failure_rate, merge_failure_rate, loop_findings, independent_ready) is read but never moves the limit. Layers merge mark by mark: `ddflow config --set schedule.signals.load_per_core.high 0.8` keeps the low mark. An unknown signal name is refused with this list by `config --set`; a table that is not valid in a config FILE does not stop ddflow loading: it is reported (stderr, doctor) and the strictest fallback applies -- every signal enabled, each mark the lower of the layers below and the shipped one.",
)
_doc(
    "schedule",
    "ready_policy",
    "'deps_and_lease' (default) hides items another agent already leased; 'deps_only' shows them, for a human planning view.",
)
_doc(
    "schedule",
    "cycle_policy",
    "What to do when the dependency graph has a cycle. 'error' refuses to schedule, naming the cycle; 'warn' schedules the rest.",
)
_doc(
    "schedule",
    "unknown_dep_policy",
    "A dependency on an id that does not exist. 'block' treats it as unmet so typos surface loudly; 'warn' ignores it. Blocking is the safe default.",
)
