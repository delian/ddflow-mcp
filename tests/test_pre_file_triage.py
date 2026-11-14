"""Pre-file triage ships in the default rule surface (RL-file-pretriage).

Pre-file triage must be a DEFAULT ddflow rule, packaged into the two surfaces every
managed project gets on onboarding — the AGENTS.md rule block and the shipped help
text — not a project-local opt-in. This pins BOTH surfaces so the checklist cannot be
dropped from either while some test still greets it elsewhere.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ddflow.services import help as H
from ddflow.services.adopt._rules import project_body

BLOCK = project_body()
WORKFLOW = H.render_topic("workflow")


def test_the_managed_block_ships_the_triage_checklist():
    # The compact form: items 1-5 apply to a task or a bug, and the file-it clause
    # (item 6) names the phase + --needs + no-deps a task carries; the wave tag is
    # stamped, so it is spelled out in the help surface instead. Flat-join the wrapped
    # line breaks so a phrase split across one still matches.
    flat = " ".join(BLOCK.split())
    for phrase in (
        "task or bug",
        "closable overlap",
        "check what is planned",
        "reuse core modules",
        "prior research",
        "person-decisions",
        "--needs",
        "no-deps",
        "fix task",
    ):
        assert phrase in flat, f"the managed block lost {phrase!r}"


def test_the_workflow_help_ships_the_full_triage_checklist_as_a_numbered_list():
    for must in (
        "## Pre-file triage",
        "1. Check existing tasks and bugs",
        "2. Check what is already planned",
        "3. Identify reusable, extensible core modules",
        "4. Reuse prior research and recorded best practice",
        "5. Ask the operator only for person-decisions",
        "6. File a task in the right phase (create one if none fits) with",
        "`no-deps` tag when it has no prerequisite",
        "A bug's phase and `--needs` go on its fix task",
    ):
        assert must in WORKFLOW, f"the workflow help lost {must!r}"
    # Item 6 must keep the three slots the shipped rule requires (r-task-phase-needs-wave):
    # --needs, the no-deps tag, and the phase (created when none fits).
    for phrase in ("its `--needs`", "the right phase", "`no-deps` tag", "fix task"):
        assert phrase in WORKFLOW, f"item 6 lost {phrase!r}"


def test_the_managed_block_stays_shorter_than_its_budget_after_triage():
    # The block is deliberately tiny (tests/test_adopt.py pins < 400 words); adding the
    # checklist to it without trimming would silently bloat it over the cap, and this
    # guard makes that failure loud HERE rather than in the adopt test only.
    assert len(BLOCK.split()) < 400
