"""A heading claims its work finished by a STATUS, not by using the word.

home-simulator's `### 36.5 — Orchestration recipes beyond the shipped two` was reported
by every `import` and `import --verify` as a phase that "say[s] the work is finished while
a task under them is still open" -- a permanent false alarm on an onboarded project, telling the operator
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


def _claims(heading: str) -> bool:
    """What production asks: the TITLE, bold already stripped by `_clean_title`."""
    return IM._claims_done(IM._clean_title(heading))


@pytest.mark.parametrize(
    "heading",
    [
        "36.5 — Orchestration recipes beyond the shipped two (P2, size M)",
        "146 — Definition of done",
        "137.E.2 — the rollout producer (NOT shipped in 137.E; added 2026-08-03)",
        "162.C — ticked-but-never-shipped has NO detector",
        "141.F — audit deferrals hidden inside completed items",
        'Phase 43 — cleanup ("make sure all incomplete work is completed")',
        "Phase 62 — LLM Orchestrator (closed-loop autonomous training)",
        "Closed-loop evaluation",
        "Done when the tests pass",
        "Shipped-vs-ticked drift detector",
        "Phase 9 — foo (NOT DONE)",
        "137.E.2 — NOT SHIPPED in 137.E",
        "Phase 9 (NOT YET SHIPPED)",
        "Phase 9 (not SHIPPED)",
        "Phase 9 — NOT  DONE",
        "Phase 20 — launch (closed beta)",
        "Parser: complete rewrite",
        "Importer — closed questions",
        "Phase 3 — Work to be done: parser rewrite",
        "Tasks completed: 3 of 12",
        "What's shipped — and what isn't",
        "DEFINITION OF DONE",
        "PHASE 4 — COMPLETE REWRITE OF PARSER",
        "Phase 4 — done in 3 days",
    ],
)
def test_the_word_in_prose_is_not_a_status(heading):
    assert not _claims(heading), heading


@pytest.mark.parametrize(
    "heading",
    [
        "Phase 99 — depth-recurrent — **SHIPPED 2026-06-01**",
        "Phase 38 — device packs ✅",
        "Phase 34 — ecosystem expansion ✅ CLOSED",
        "Review (V6 session, closed 2026-07-12)",
        "Review — Session AUTODEDUPE (roborev job 191), closed 2026-08-07",
        "Completed (current)",
        "Phase 7 — done",
        "Phase 8 (shipped)",
        "Phase 5 — foo (P1) — shipped in 0.3",
        "Phase 5 — foo — Shipped (v1)",
        "Phase 5 — foo: shipped in v2",
        "Phase 5 — foo, now shipped",
        "Phase 5 — foo ✔ done",
        "Phase 12 SHIPPED — docs NOT DONE",
        "Phase 9 — done.",
        "Phase 9 (closed!)",
        "PHASE 5 — DONE",
        "PHASE 12 SHIPPED",
        "PHASE 3 COMPLETE (2026-08-01)",
    ],
)
def test_a_status_still_is(heading):
    assert _claims(heading), heading


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


@pytest.mark.parametrize(
    "heading",
    ["36.9 — Gates ✅ SHIPPED", "36.9 — Gates — shipped in 0.3", "36.9 — Gates (done)"],
)
def test_the_drift_note_names_a_status_heading_end_to_end(repo, heading):
    p = repo / "docs" / "todo.md"
    p.parent.mkdir(parents=True)
    p.write_text(f"## Phase 36\n### {heading}\n- [ ] **36.9a** x\n")
    notes = " ".join(IM.plan_import(repo, None).notes)
    assert "say the work is finished" in notes and "36.9" in notes, notes


def test_import_verify_reports_drift_for_a_status_heading_only(repo):
    """The `--verify` call site, end to end: the prose heading is not drift."""
    import json

    from conftest import run_cli

    p = repo / "docs" / "todo.md"
    p.parent.mkdir(parents=True)
    p.write_text(
        "## Phase 36\n### 36.5 — Orchestration recipes beyond the shipped two\n"
        "- [ ] **36.5a** four recipes\n\n### 36.9 — Gates ✅ SHIPPED\n- [ ] **36.9a** x\n"
    )
    assert run_cli(repo, "import", "--apply")[0] == 0
    _code, out, err = run_cli(repo, "--json", "import", "--verify")
    drift = json.loads(out)["shipped_with_open_tasks"]
    assert drift == ["36.9"], (drift, err)
