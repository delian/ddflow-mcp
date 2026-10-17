"""The `[chore]` section: the class of files `ddflow chore commit` may land without a reviewer.

Re-exported from `ddflow.config`, which assembles `Config` from every section (decision
D-chore-class)."""

from __future__ import annotations

from dataclasses import dataclass

from ._docs import declare, knob


@declare("chore")
@dataclass
class ChoreConfig:
    """Chores: files a person has approved to be committed, merged and completed with no
    reviewer (`ddflow chore commit`)."""

    globs: list[str] = knob(
        factory=lambda: [
            ".ddflow/rules/**",
            ".mailmap",
            "DDFLOW.md",
            "docs/ddflow/decisions/**",
            "docs/ddflow/QUEUE.md",
            "docs/ddflow/LESSONS.md",
            "docs/ddflow/LESSONS-SUMMARY.md",
        ],
        doc="The chore class: files `ddflow chore commit FILES -m MSG` may commit, merge and COMPLETE with no reviewer (rules, .mailmap, DDFLOW.md, decision records, generated views). A change to any file outside the list is refused and takes the normal pipeline. The list is approved by a person: `ddflow chore approve` records the approval of THIS list (an agent identity cannot), so a changed list needs approving again before a chore may use it, whoever edited it.",
    )
    skip_gates: list[str] = knob(
        factory=lambda: [
            "research",
            "rules",
            "implement",
            "rubber_duck",
            "critic",
            "verify",
            "standards",
            "ci",
            "unit_tests",
            "bug_hunt",
            "dedupe",
        ],
        doc="The gates a chore skips, each recorded with its reason (the chore, the matched globs and the approval). A gate of the item's pipeline that is neither listed here nor `merge` (which the merge itself records) is left for you to run, and the chore does not complete until it carries an outcome.",
    )
