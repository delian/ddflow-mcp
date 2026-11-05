"""Does a selected ci run take the host-wide suite slot (Bc07bf8feb4)?

On what it would cost, not on how many files it names. ddflow keeps no per-test durations; what
it does record, on every `ci` gate, is the run's seconds, its queue wait, the seconds of its
always-run guards and the workers its selection ran on. That gives each past run a rate, in
worker-seconds per selected file. The median rate, times the files of this selection, over the
workers `workers.compute` gives it now, is the run's predicted seconds: a loaded host grants fewer
workers, so the same selection predicts longer, and the load decides through the one governor.

With too few measured runs there is nothing to predict from, and the count of the selected files
decides. The always-run guards count for neither: every item pays them, so they are no measure of
the change. The decision is recorded as it was reached (``basis``), so a count is never read as a
measurement."""

from __future__ import annotations

import statistics
from collections.abc import Iterable, Sequence
from typing import Any

from .. import workers as WK
from ..gates.testcmd import counts_of

#: The verdicts of a run that ended. `gate.unavailable` measured nothing: it timed out, so its
#: clock stopped at the limit, or it never started.
FINISHED = frozenset({"gate.passed", "gate.failed", "gate.partial"})
#: `ddflow ci run` exit codes of a run that reached its verdict (2: it could not run).
VERDICT_EXITS = (0, 1)


def _seconds(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _selection_finished(evidence: dict[str, Any], ran: Any) -> bool:
    """Did the selected tests run to their end? They passed (``ran`` True), or they ran and
    pytest's summary counts a failure: its seconds are real. Red without one is a collection
    error, which stops the run; `failed_tests` cannot tell, it holds ERROR rows too."""
    if ran is not False:
        return ran is True
    return any(counts_of(str(line)).get("failed") for line in evidence.get("summary") or ())


def _rate(evidence: dict[str, Any]) -> float | None:
    """The worker-seconds per selected file of one recorded run; None when the record does not
    measure one."""
    scope = evidence.get("ci_scope") or {}
    used = (scope.get("workers") or {}).get("selected") or {}
    took, files = _seconds(evidence.get("elapsed_s")), _seconds(scope.get("selected"))
    workers = _seconds(used.get("n"))
    waited, guards = _seconds(evidence.get("waited_s")), _seconds(scope.get("always_s"))
    if (
        scope.get("scope") != "selected"
        or not _selection_finished(evidence, (scope.get("ran") or {}).get("selected"))
        or evidence.get("exit") not in VERDICT_EXITS
        or not (took and files and workers)
        or guards is None  # their seconds are in `took`, and nothing says how many
        or (waited is None and "suite_floor" in used)  # a holder, its queue time unknown
    ):
        return None
    alone = took - (waited or 0.0) - guards
    return alone * workers / files if alone > 0 else None


def measured_rates(events: Sequence[Any], cfg: Any) -> list[float]:
    """The rates of the newest `[ci].selected_lock_runs` runs that measured one, newest first."""
    window = max(1, int(cfg.ci.selected_lock_runs))
    rates: list[float] = []
    for e in reversed(events):
        if e.kind not in FINISHED or e.data.get("gate") != "ci":
            continue
        rate = _rate(e.data.get("evidence") or {})
        if rate is not None:
            rates.append(rate)
            if len(rates) >= window:
                break
    return rates


def decide(cfg: Any, files: int, rates: Iterable[float]) -> dict[str, Any]:
    """Whether a selection of ``files`` test files takes the slot (``slot``), and on what."""
    ci, rates = cfg.ci, list(rates)
    count_limit, time_limit = int(ci.selected_lock_tests), int(ci.selected_lock_s)
    if count_limit <= 0:
        return {"slot": False, "basis": "off", "files": files}
    needed = max(1, int(ci.selected_lock_min_runs))
    if time_limit > 0 and len(rates) >= needed:
        rate = statistics.median(rates)
        workers = WK.compute(cfg, files)
        predicted = rate * files / workers.n
        return {
            "slot": predicted > time_limit,
            "basis": "measured",
            "files": files,
            "predicted_s": round(predicted, 1),
            "limit_s": time_limit,
            "rate_s": round(rate, 1),
            "runs": len(rates),
            "workers": workers.n,
            "workers_rule": workers.inputs.get("rule"),
            "load_per_core": workers.inputs.get("load_per_core"),
        }
    why = (
        f"{len(rates)} measured run(s), {needed} needed" if time_limit > 0 else "selected_lock_s=0"
    )
    return {"slot": files > count_limit, "basis": "file_count", "files": files,
            "limit": count_limit, "why": why}  # fmt: skip
