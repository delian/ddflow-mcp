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
from ..core.model import State

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


def gate_rates(events: list[Event]) -> dict[str, GateRate]:
    """Per-gate outcome counts, from the raw log.

    Raw events rather than folded state, because `State.gates` keeps the LAST outcome per
    item and a rate needs the history. The same reason `progress.work` reads events.
    """
    out: dict[str, GateRate] = {}
    for ev in events:
        if not ev.kind.startswith("gate.") or ev.kind == "gate.started":
            continue
        outcome = ev.kind.split(".", 1)[1]
        if outcome == "out_of_order":
            continue  # a pipeline-order complaint, not a verdict about the work
        gate = ev.data.get("gate") or ev.subject
        rate = out.setdefault(gate, GateRate(gate))
        rate.outcomes[outcome] = rate.outcomes.get(outcome, 0) + 1
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

    @property
    def expected(self) -> int:
        """How many times it should have fired by now."""
        return self.count // self.every if self.every > 0 else 0

    @property
    def missed(self) -> int:
        """Scheduled minus fired, never negative — running EARLY is not a defect."""
        return max(0, self.expected - self.ran)

    def render(self) -> str:
        return (
            f"{self.name}: fired {self.ran} of {self.expected} scheduled "
            f"({self.count} {self.unit} completed, every {self.every})"
        )


def cadence_rates(state: State, cfg: Config) -> list[CadenceRate]:
    """Every cadence's hit rate, computed the same way due-ness is.

    Exact rather than approximate, because a cadence here is counted in COMPLETIONS, not
    wall-clock: `expected` is `completed // every`, which is a fact about the log. A
    wall-clock cadence could only be estimated, and an estimate is what nobody argues from.
    """
    done_tasks = sum(1 for i in state.items.values() if i.kind == "task" and i.state == "done")
    done_phases = sum(1 for i in state.items.values() if i.kind == "phase" and i.state == "done")
    c = cfg.cadence
    spec = (
        ("integration_tests", c.integration_tests_every_tasks, "tasks", done_tasks),
        ("dedupe_sweep", c.dedupe_sweep_every_tasks, "tasks", done_tasks),
        ("architecture_review", c.architecture_review_every_phases, "phases", done_phases),
        ("mutation_tests", c.mutation_tests_every_phases, "phases", done_phases),
        ("lessons_pass", c.lessons_pass_every_phases, "phases", done_phases),
    )
    return [
        CadenceRate(name, every, unit, count, len(state.cadences.get(name, [])))
        for name, every, unit, count in spec
    ]


def never_fired(state: State, cfg: Config) -> list[CadenceRate]:
    """Cadences that have fallen far enough behind to be worth saying out loud.

    `cadence.max_missed` rather than "any miss": a pass that is one period behind is simply
    due, which `ddflow cadence` already reports. This is for the case the source project
    hit — scheduled repeatedly, fired never, and nothing anywhere said so.
    """
    return [r for r in cadence_rates(state, cfg) if r.missed > cfg.cadence.max_missed]
