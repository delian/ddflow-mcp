"""The `[self_assessment]` section: ddflow auto-files what an agent's self-assessment names.

Rule `r-ddflow-self-assessment` (decision `D-ddflow-self-assessment-r2`) tells every agent
to assess ddflow itself at the end of a run, each phase and before compaction, and to file
what it found. That rule was ADVISORY -- nothing in the default pipeline scanned an
assessment and filed its named defects, so whether a found bug or gap reached the queue
depended on each agent complying. `[self_assessment]` makes the filing a default pass,
off-able, so it does not depend on compliance."""

from __future__ import annotations

from dataclasses import dataclass

from ._docs import declare, knob


@declare("self_assessment")
@dataclass
class SelfAssessmentConfig:
    """The defensive pass that files a bug or task for each defect a self-assessment names."""

    auto_file: bool = knob(
        True,
        doc="Whether the defensive self-assessment pass runs by default (default true): at session end, at phase end, before context compaction, and whenever assessment text names a defect, each not-yet-tracked defect it names is filed as a bug (`bug found --scope ddflow ...`) and one that reads like an existing record is skipped, never duplicated. A bug filed this way is filed with `--no-task` when the fix already ships in the finding commit. Turn it off per layer (later wins): the code default is overridden by `<repo>/.ddflow/config.toml`, then `<repo>/.ddflow/local/config.toml`, then `DDFLOW_SELF_ASSESSMENT_AUTO_FILE`.",
    )
