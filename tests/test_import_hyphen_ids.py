"""Bug B-import-hyphen-ids: hyphenated ids (`L-12`, `R-7`) were read as slugs.

`_LESSON_HEAD` matched `L\\d+` only, so `## L-12 — ...` imported as `L-l-12-...`, a research
entry `## R-7 — ...` as `R-r-7-...`, "see L-12" resolved to nothing, and a lessons-summary
bullet citing `(L-12)` was filed as a second lesson tagged `summary` instead of becoming
that lesson's summary. One id grammar now serves the heading and the citation, and an
entry already imported under its old slug is recognised rather than imported twice.
"""

from __future__ import annotations

import json
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


def _pre_fix_import(repo: Path) -> None:
    """The events the importer wrote BEFORE the fix, shaped as `apply_import` writes them:
    a slug id, the human heading as the title, the source line in `seen_in`/`sources`.
    (Research ids were never kept before, so `R8` was slugged too.)"""
    log = EventLog(repo, agent_id="ddflow-import")
    for line, heading in (
        (3, "L-12 — Agent branches must be rebased"),
        (7, "L-13 — Never force a push"),
    ):
        log.append(
            "lesson.recorded",
            f"L-{IM._slug(heading, 32)}",
            {
                "title": heading,
                "rule": "x",
                "tags": ["imported"],
                "seen_in": [f"docs/lessons.md:{line}"],
            },
        )
    for line, heading in (
        (3, "R-7 — Does the cache survive a restart"),
        (7, "R8 — Unhyphenated research id"),
    ):
        log.append(
            "research.recorded",
            f"R-{IM._slug(heading, 32)}",
            {
                "question": heading,
                "claim": "x",
                "verdict": "CONFIRMED",
                "sources": [f"docs/RESEARCH.md:{line}"],
                "tags": ["imported"],
            },
        )
    # L14 was an id before the fix too.
    log.append(
        "lesson.recorded",
        "L14",
        {
            "title": "Unhyphenated ids still work",
            "rule": "x",
            "tags": ["imported"],
            "seen_in": ["docs/lessons.md:11"],
        },
    )


def test_a_re_import_over_the_old_slug_ids_imports_nothing_twice(repo):
    """A log imported before the fix holds `L-l-12-...` and `R-r-7-...`; a re-run must
    recognise them rather than import each a second time under its kept id."""
    _corpus(repo, summary=False)
    run_cli(repo, "init")
    _pre_fix_import(repo)
    plan = IM.plan_import(repo, fold(EventLog(repo).read_all()))
    new = sorted(f.ident for f in plan.found if f.kind in ("lesson", "research"))
    assert new == [], new
    _rc, out, err = run_cli(repo, "import")
    assert "Nothing to import" in out, (out, err)


def test_import_verify_reports_no_drift_over_a_pre_fix_import(repo):
    _corpus(repo, summary=False)
    run_cli(repo, "init")
    _pre_fix_import(repo)
    rc, out, err = run_cli(repo, "--json", "import", "--verify")
    report = json.loads(out)
    assert report["new_since_import"] == [], report["new_since_import"]
    assert rc == 0, (out, err)


def test_an_exact_citation_still_resolves_when_both_spellings_exist(tmp_path):
    """`L12` and `L-12` as two lessons: each exact citation resolves to its own."""
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "lessons.md").write_text(
        "# Lessons\n\n## L12 — Caching\n\nBody.\n\n## L-12 — Sockets\n\nBody.\n"
    )
    (tmp_path / "docs" / "lessons-summary.md").write_text(
        "# Summary\n\n- **Cache.** the caching rule [L12]\n- **Sock.** the socket rule [L-12]\n"
    )
    plan = IM.plan_import(tmp_path)
    lessons = {f.ident: f for f in plan.found if f.kind == "lesson"}
    assert sorted(lessons) == ["L-12", "L12"], sorted(lessons)
    assert "caching rule" in lessons["L12"].extra["summary"]
    assert "socket rule" in lessons["L-12"].extra["summary"]


def test_a_title_that_merely_starts_like_an_id_is_not_one(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "RESEARCH.md").write_text(
        "# Research\n\n## R2-D2 — droid protocol\n\nx\n\n## R2 — real entry\n\ny\n"
    )
    ids = _ids(IM.plan_import(tmp_path), "research")
    assert "R2" in ids and not any(i.startswith("R2-") for i in ids), ids
