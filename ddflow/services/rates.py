"""Do ddflow's own mechanisms fire, and how often? (B24, B25)

Two measurements about the tool rather than about the work, and both exist because an
unmeasured mechanism is indistinguishable from a missing one:

* **B24, per-gate fire rate.** "A gate that fails on everything is worse than no gate: it
  trains the next reader to skip it." A gate failing on nearly every run is either flaky or
  measuring a moving target, and re-running it will not converge.
* **B25, cadence fired-vs-scheduled.** "The mechanism you did not measure is the one that
  is not running" — on the source project three wakeups were scheduled and zero ever fired,
  producing a 12-hour stall while an undesignated mechanism did the work. The due-ness
  computation already existed here; what was missing was any way to argue about the
  cadence's own hit rate.

Both are derived, never stored: gate rates from the event log, cadence rates from the
folded state plus the config. There is no counter to reset and none to drift.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..config import Config
from ..core.events import Event
from ..core.model import GATE_OUTCOMES, State
from .schedule import count_at_last_run, count_passes

#: Gate outcomes that mean "this gate said no". `skipped` is deliberately NOT here: a
#: skipped gate did not fire, and counting it as a failure would make an unconfigured gate
#: look like a broken one.
_NEGATIVE = ("failed",)


@dataclass
class GateRate:
    """How often one gate has passed, failed or been skipped, across the whole queue."""

    gate: str
    outcomes: dict[str, int] = field(default_factory=dict)

    @property
    def runs(self) -> int:
        """Decisive runs only — a skip is not a run, it is the absence of one."""
        return sum(n for k, n in self.outcomes.items() if k != "skipped")

    @property
    def failed(self) -> int:
        return sum(self.outcomes.get(k, 0) for k in _NEGATIVE)

    @property
    def skipped(self) -> int:
        return self.outcomes.get("skipped", 0)

    @property
    def fail_rate(self) -> float:
        """Share of decisive runs that said no. 0.0 when it has never decided anything."""
        return self.failed / self.runs if self.runs else 0.0


def gate_rates(source: State | list[Event]) -> dict[str, GateRate]:
    """Per-gate outcome counts across the whole queue.

    From the folded state's `Item.gate_history` -- every outcome recorded, not the LAST
    per gate that `Item.gates` keeps, because a rate needs the history. A caller that has
    only the raw events (`api/reporting/health.py` until it passes its state) gets the
    same count from one pass over them: both read "a recorded outcome" as the gate events
    whose kind is an outcome -- `started` and `out_of_order` are not verdicts about the
    work.
    """
    out: dict[str, GateRate] = {}

    def count(gate: str, outcome: str) -> None:
        rate = out.setdefault(gate, GateRate(gate))
        rate.outcomes[outcome] = rate.outcomes.get(outcome, 0) + 1

    if isinstance(source, State):
        for item in source.items.values():
            for run in item.gate_history:
                count(run.gate or item.id, run.outcome)
        return out
    for ev in source:
        outcome = ev.kind.split(".", 1)[1] if ev.kind.startswith("gate.") else ""
        if outcome in GATE_OUTCOMES:
            count(ev.data.get("gate") or ev.subject, outcome)
    return out


@dataclass
class GateFinding:
    gate: str
    detail: str


def failing_gates(rates: dict[str, GateRate], cfg: Config) -> list[GateFinding]:
    """Gates that say no so often that nobody will keep reading them.

    Requires a MINIMUM number of runs before judging: one failure out of one run is 100%
    and means nothing, and a check that cries wolf on a new gate's first red is the very
    thing this is here to prevent.
    """
    thresh = cfg.gates.rate_max_fail
    out = []
    for gate, rate in sorted(rates.items()):
        if rate.runs < cfg.gates.rate_min_runs:
            continue
        if rate.fail_rate < thresh:
            continue
        out.append(
            GateFinding(
                gate,
                f"failed {rate.failed} of {rate.runs} runs ({rate.fail_rate:.0%}, threshold "
                f"{thresh:.0%}) — a gate that fails on nearly everything is flaky or is "
                f"measuring a moving target, and re-running it will not converge",
            )
        )
    return out


@dataclass
class CadenceRate:
    """Fired versus scheduled, for one periodic pass."""

    name: str
    every: int
    unit: str
    count: int  #: completions of `unit` so far
    ran: int  #: `cadence.ran` events recorded
    at_last: int  #: the completion count recorded BY the most recent run; 0 if never run

    @property
    def since(self) -> int:
        """Completions since it last fired — or since the beginning if it never has.

        This is the SAME quantity `api.cadence` uses to decide due-ness, and it has to be:
        two measures of "is this pass behind" that can disagree is a situation nobody can
        reason about, and the first version of this class had exactly that. It computed
        `expected = count // every` and compared it to the number of runs, which assumes
        every run happened at its scheduled point. A cadence that fired three times EARLY
        and then stopped therefore had `ran (3) > expected (1)`, so `missed` floored to 0
        and `doctor` stayed silent while `ddflow cadence` reported it DUE — the exact
        failure B25 exists to detect, hidden by its own arithmetic. Found by roborev on
        9234cdb, reproduced against both surfaces.
        """
        return max(0, self.count - self.at_last)

    @property
    def overdue_periods(self) -> int:
        """How many scheduled firings have passed since it last fired.

        0 = up to date, 1 = simply due (which `ddflow cadence` already says), 2+ = it has
        been skipped, which is what `stalled()` reports.
        """
        return self.since // self.every if self.every > 0 else 0

    @property
    def expected(self) -> int:
        """Total firings the schedule has called for. Informational only — do NOT decide
        behind-ness from this, see `since`."""
        return self.count // self.every if self.every > 0 else 0

    def render(self) -> str:
        last = f"last at {self.at_last}" if self.ran else "NEVER fired"
        return (
            f"{self.name}: fired {self.ran}x ({last}), {self.since} {self.unit} since — "
            f"{self.overdue_periods} scheduled run(s) skipped (every {self.every})"
        )


def cadence_rates(state: State, cfg: Config) -> list[CadenceRate]:
    """Every cadence's hit rate, computed the same way due-ness is.

    Exact rather than approximate, because a cadence here is counted in COMPLETIONS, not
    wall-clock: `expected` is `completed // every`, which is a fact about the log. A
    wall-clock cadence could only be estimated, and an estimate is what nobody argues from.
    """
    return [
        CadenceRate(
            p.name,
            p.every,
            p.unit,
            p.count,
            len(state.cadences.get(p.name, [])),
            # What `count_due` reads, so due-ness and this rate cannot disagree.
            count_at_last_run(state, p.name),
        )
        for p in count_passes(state, cfg)
    ]


def stalled(state: State, cfg: Config) -> list[CadenceRate]:
    """Cadences that have fallen far enough behind to be worth saying out loud.

    `cadence.max_missed` rather than "any miss": one period behind is simply DUE, which
    `ddflow cadence` already reports, and a check that repeats another check's output is one
    nobody reads twice. This is for the case the source project hit — scheduled repeatedly,
    fired never, and nothing anywhere said so.

    Named `stalled`, not `never_fired`: it also catches a pass that fired for a while and
    then stopped, which is the commoner and quieter version. The old name went into a
    `doctor` label that read "cadence never fired" about a cadence that plainly had.
    """
    return [r for r in cadence_rates(state, cfg) if r.overdue_periods > cfg.cadence.max_missed]
