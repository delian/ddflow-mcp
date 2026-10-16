"""The `[cadence]` section: its dataclass, the values it accepts and its knob docs.

Re-exported from `ddflow.config`, which assembles `Config` from every section."""

from __future__ import annotations

from dataclasses import dataclass

from ._docs import declare, knob


@declare("cadence")
@dataclass
class CadenceConfig:
    """Periodic whole-repo passes that a per-task gate structurally cannot do."""

    integration_tests_every_tasks: int = knob(
        5,
        doc="Run the FULL test suite (`ddflow ci run --full`) after this many completed tasks. An item's ci and unit_tests gates run only the tests its change reaches (D-test-economy-r2), so this pass, the merge queue's batch run and the phase-end acceptance are where the whole suite runs and cross-task interaction regressions show.",
    )
    architecture_review_every_phases: int = knob(
        2,
        doc="How often to run a whole-repo architecture/complexity review. Catches structural drift no per-diff reviewer can see.",
    )
    mutation_tests_every_phases: int = knob(
        3,
        doc="How often to mutation-test the suite. A green suite that survives mutation is measuring patience, not coverage.",
    )
    dedupe_sweep_every_tasks: int = knob(
        4,
        doc="How often to run the cross-file duplication sweep. Per-task dedupe sees only its own diff; drift between two copies needs a repo-wide comparison.",
    )
    lessons_pass_every_phases: int = knob(
        4,
        doc="How often to consider a lessons compression pass, in addition to the growth trigger.",
    )
    max_missed: int = knob(
        1,
        doc="How many scheduled runs a cadence may have skipped before `doctor` reports it. 1 (default) means 'being due is not a finding -- never firing is'. A cadence scheduled repeatedly that has fired zero times is the failure this measures: on the project ddflow was extracted from, three wakeups were scheduled, none fired, and the 12-hour stall was only noticed because an undesignated mechanism did the work instead.",
    )
    #: `name=days` for passes that are due by the CALENDAR, not by completions.
    every_days: list[str] = knob(
        factory=list,
        doc='Passes that fall due by the calendar rather than by completed work, as `name=days`: ["bug_hunt=7", "dedupe_sweep=7"]. A name equal to a count-based pass (dedupe_sweep, integration_tests, ...) REPLACES it. `ddflow cadence` reports one due when `cadence --ran <name>` has not been recorded within that many days -- or ever, so a weekly pass that has never run is due now rather than silently never. For a rule like \'a bug hunt every week\' that otherwise lives only in prose, which is where it stops happening.',
    )
