"""Bug B-import-hyphen-ids: hyphenated ids (`L-12`, `R-7`) were read as slugs.

`_LESSON_HEAD` matched `L\\d+` only, so `## L-12 — ...` imported as `L-l-12-...`, a research
entry `## R-7 — ...` as `R-r-7-...`, "see L-12" resolved to nothing, and a lessons-summary
bullet citing `(L-12)` was filed as a second lesson tagged `summary` instead of becoming
that lesson's summary. One id grammar now serves the heading and the citation, and an
entry already imported under its old slug is recognised rather than imported twice.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.core.model import fold
from ddflow.infra.log import EventLog
from ddflow.services import importer as IM

LESSONS = """# Lessons

## L-12 — Agent branches must be rebased

Rebase before merging. See L-13.

## L-13 — Never force a push

Body two.

## L14 — Unhyphenated ids still work

Body three.
"""

SUMMARY = """# Summary

- **Rebase agent branches.** keep them fresh (L-12)
- **Unhyphenated ids.** cited with a hyphen (L-14)
"""

RESEARCH = """# Research

## R-7 — Does the cache survive a restart

CONFIRMED by a probe.

## R8 — Unhyphenated research id

REFUTED.
"""


def _corpus(repo: Path, *, summary: bool = True) -> None:
    (repo / "docs").mkdir(exist_ok=True)
    (repo / "docs" / "lessons.md").write_text(LESSONS)
    (repo / "docs" / "RESEARCH.md").write_text(RESEARCH)
    if summary:
        (repo / "docs" / "lessons-summary.md").write_text(SUMMARY)


def _ids(plan: IM.ImportPlan, kind: str) -> list[str]:
    return sorted(f.ident for f in plan.found if f.kind == kind)


def test_hyphenated_lesson_and_research_ids_are_kept_as_ids(tmp_path):
    _corpus(tmp_path, summary=False)
    plan = IM.plan_import(tmp_path)
    assert _ids(plan, "lesson") == ["L-12", "L-13", "L14"], _ids(plan, "lesson")
    assert _ids(plan, "research") == ["R-7", "R8"], _ids(plan, "research")
    titles = {f.ident: f.title for f in plan.found}
    assert titles["L-12"] == "Agent branches must be rebased", titles


def test_a_summary_bullet_citing_a_hyphenated_id_becomes_that_lessons_summary(tmp_path):
    _corpus(tmp_path)
    plan = IM.plan_import(tmp_path)
    lessons = {f.ident: f for f in plan.found if f.kind == "lesson"}
    assert sorted(lessons) == ["L-12", "L-13", "L14"], "a cited bullet became a lesson of its own"
    assert "keep them fresh" in lessons["L-12"].extra["summary"]
    # `L-14` and `L14` are the same lesson: the spelling of a citation is not identity.
    assert "cited with a hyphen" in lessons["L14"].extra["summary"]


def test_a_re_import_over_the_old_slug_ids_imports_nothing_twice(repo):
    """A log imported before the fix holds `L-l-12-...` and `R-r-7-...`; a re-run must
    recognise them, and `import --verify` must not report them as drift."""
    _corpus(repo, summary=False)
    run_cli(repo, "init")
    log = EventLog(repo, agent_id="old-importer")
    for kind, slug in (
        ("lesson", f"L-{IM._slug('L-12 — Agent branches must be rebased', 32)}"),
        ("lesson", f"L-{IM._slug('L-13 — Never force a push', 32)}"),
        ("research", f"R-{IM._slug('R-7 — Does the cache survive a restart', 32)}"),
        ("research", f"R-{IM._slug('R8 — Unhyphenated research id', 32)}"),
    ):
        if kind == "lesson":
            log.append("lesson.recorded", slug, {"title": slug, "rule": "x"})
        else:
            log.append("research.recorded", slug, {"question": slug, "verdict": "CONFIRMED"})
    state = fold(log.read_all())
    plan = IM.plan_import(repo, state)
    new = sorted(f.ident for f in plan.found if f.kind in ("lesson", "research"))
    assert new == ["L14"], new
