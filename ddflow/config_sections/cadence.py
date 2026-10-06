"""The `[cadence]` section: its dataclass, the values it accepts and its knob docs.

Re-exported from `ddflow.config`, which assembles `Config` from every section."""

from __future__ import annotations

from dataclasses import dataclass, field

from ._docs import _doc


@dataclass
class CadenceConfig:
    """Periodic whole-repo passes that a per-task gate structurally cannot do."""

    integration_tests_every_tasks: int = 5
    architecture_review_every_phases: int = 2
    mutation_tests_every_phases: int = 3
    dedupe_sweep_every_tasks: int = 4
    lessons_pass_every_phases: int = 4
    max_missed: int = 1
    #: `name=days` for passes that are due by the CALENDAR, not by completions.
    every_days: list[str] = field(default_factory=list)


_doc(
    "gates",
    "rate_min_runs",
    "How many DECISIVE runs a gate needs before its failure rate is judged. One failure out of one run is 100% and means nothing, so a low value turns a new gate's first red into a finding — which is the crying-wolf failure this check exists to prevent. A skipped gate is not a run.",
)
_doc(
    "gates",
    "rate_max_fail",
    "Failure rate (0.0-1.0) at which a gate is reported as failing on nearly everything. 'A gate that fails on everything is worse than no gate: it trains the next reader to skip it.' At or above this, the gate is flaky or measuring a moving target and re-running it will not converge -- the remedy is to repair the gate, not the work.",
)
_doc(
    "cadence",
    "every_days",
    'Passes that fall due by the calendar rather than by completed work, as `name=days`: ["bug_hunt=7", "dedupe_sweep=7"]. A name equal to a count-based pass (dedupe_sweep, integration_tests, ...) REPLACES it. `ddflow cadence` reports one due when `cadence --ran <name>` has not been recorded within that many days -- or ever, so a weekly pass that has never run is due now rather than silently never. For a rule like \'a bug hunt every week\' that otherwise lives only in prose, which is where it stops happening.',
)
_doc(
    "cadence",
    "max_missed",
    "How many scheduled runs a cadence may have skipped before `doctor` reports it. 1 (default) means 'being due is not a finding -- never firing is'. A cadence scheduled repeatedly that has fired zero times is the failure this measures: on the project ddflow was extracted from, three wakeups were scheduled, none fired, and the 12-hour stall was only noticed because an undesignated mechanism did the work instead.",
)
_doc(
    "reinstruct",
    "enabled",
    "Whether tool results may carry a short footer naming what this project has left undone. The instruction block is delivered once at connect; after a context compaction nothing else re-states it, and MCP has no primitive for injecting context. Set false to silence it entirely.",
)
_doc(
    "reinstruct",
    "every_calls",
    "Minimum tool calls between two footers. A footer on every call is a banner readers learn to skip.",
)
_doc(
    "reinstruct",
    "every_seconds",
    "Minimum seconds between two footers. BOTH this and every_calls must be satisfied, so a burst of calls does not produce a burst of footers.",
)
_doc(
    "reinstruct",
    "max_items",
    "How many outstanding obligations a footer names. Longer than this and it is scrolled past rather than read.",
)
_doc(
    "cadence",
    "integration_tests_every_tasks",
    "Run the integration suite after this many completed tasks. Unit gates are per-task and cannot see cross-task interaction regressions.",
)
_doc(
    "cadence",
    "architecture_review_every_phases",
    "How often to run a whole-repo architecture/complexity review. Catches structural drift no per-diff reviewer can see.",
)
_doc(
    "cadence",
    "mutation_tests_every_phases",
    "How often to mutation-test the suite. A green suite that survives mutation is measuring patience, not coverage.",
)
_doc(
    "cadence",
    "dedupe_sweep_every_tasks",
    "How often to run the cross-file duplication sweep. Per-task dedupe sees only its own diff; drift between two copies needs a repo-wide comparison.",
)
_doc(
    "cadence",
    "lessons_pass_every_phases",
    "How often to consider a lessons compression pass, in addition to the growth trigger.",
)
