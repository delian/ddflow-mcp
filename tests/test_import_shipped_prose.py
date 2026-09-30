"""A heading claims its work finished by a STATUS, not by using the word.

home-simulator's `### 36.5 — Orchestration recipes beyond the shipped two` was reported
by every `import` and `import --verify` as "says the work is finished while a task under
it is still open" -- a permanent false alarm on an onboarded project, telling the operator
to decide something there is nothing to decide about. Measured over run_nemo_run's and
home-simulator's todo headings, the case-insensitive word match raised 15 such alarms
("Definition of done", "NOT shipped in 137.E", "ticked-but-never-shipped") and every
real status it caught is still caught.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ddflow.services import importer as IM


@pytest.mark.parametrize(
    "heading",
    [
        "36.5 — Orchestration recipes beyond the shipped two (P2, size M)",
        "146 — Definition of done",
        "137.E.2 — the rollout producer (NOT shipped in 137.E; added 2026-08-03)",
        "162.C — ticked-but-never-shipped has NO detector",
        "141.F — audit deferrals hidden inside completed items",
        'Phase 43 — cleanup ("make sure all incomplete work is completed")',
    ],
)
def test_the_word_in_prose_is_not_a_status(heading):
    assert not IM._DONE_MARKER.search(heading), heading


@pytest.mark.parametrize(
    "heading",
    [
        "Phase 99 — depth-recurrent — **SHIPPED 2026-06-01**",
        "Phase 38 — device packs ✅",
        "Phase 34 — ecosystem expansion ✅ CLOSED",
        "Review (V6 session, closed 2026-07-12)",
        "Review — Session AUTODEDUPE (roborev job 191), closed 2026-08-07",
        "Completed (current)",
        "P2 sweep complete — final state",
        "Phase 7 — done",
        "Phase 8 (shipped)",
    ],
)
def test_a_status_still_is(heading):
    assert IM._DONE_MARKER.search(heading), heading


def test_the_drift_note_no_longer_names_a_prose_heading(repo):
    p = repo / "docs" / "todo.md"
    p.parent.mkdir(parents=True)
    p.write_text(
        "## Phase 36\n### 36.5 — Orchestration recipes beyond the shipped two\n"
        "- [ ] **36.5a** four recipes\n\n### 36.9 — Gates ✅ SHIPPED\n- [ ] **36.9a** x\n"
    )
    notes = " ".join(IM.plan_import(repo, None).notes)
    assert "say the work is finished" in notes and "36.9" in notes
    assert "36.5 (" not in notes
