"""B-importer-dedupe: an import adds nothing twice and reports what repeats a record.

Decision D-no-duplicates, applied to the importer: identical text is not imported again,
and a record that reads like one already held -- a lessons-summary bullet that restates a
lesson, a lesson an earlier import already holds under another id -- is reported, with the
id it repeats and the score, instead of being imported silently.
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

## L1 — Agent branches must be rebased before merging

Always rebase an agent branch onto the current main before merging it, otherwise the
merge silently reverts work another agent landed in the meantime.

## L2 — Never force a push to a shared branch

Force pushing rewrites history other worktrees are built on; use a revert commit instead.
"""

# The first bullet restates L1 in other words and cites nothing; the second is new.
SUMMARY = """# Summary

## Git

- **Rebase before merging.** Always rebase an agent branch onto the current main before
  merging it, otherwise the merge silently reverts work another agent landed meanwhile.
- **Cache keys.** A cache keyed by file path alone serves stale thumbnails after a rename,
  so key it by content digest plus the rendering parameters.
"""


def _corpus(repo: Path, *, summary: bool = True) -> None:
    (repo / "docs").mkdir(exist_ok=True)
    (repo / "docs" / "lessons.md").write_text(LESSONS)
    if summary:
        (repo / "docs" / "lessons-summary.md").write_text(SUMMARY)


def _plan(repo: Path) -> IM.ImportPlan:
    return IM.plan_import(repo, fold(EventLog(repo).read_all()))


def _apply(repo: Path) -> dict[str, int]:
    plan = _plan(repo)
    return IM.apply_import(repo, EventLog(repo, agent_id="ddflow-import"), plan)


def _lessons(plan: IM.ImportPlan) -> dict[str, IM.Found]:
    return {f.ident: f for f in plan.found if f.kind == "lesson"}


def test_a_summary_bullet_that_repeats_a_lesson_is_reported_not_imported(repo):
    _corpus(repo)
    run_cli(repo, "init")
    plan = _plan(repo)
    ids = _lessons(plan)
    assert not any("rebase" in i.lower() for i in ids if i.startswith("LS-")), ids
    assert any(i.startswith("LS-") and "cache" in i for i in ids), "the new bullet imports"
    (dup,) = plan.duplicates
    assert dup.of == "L1" and dup.where == "import" and dup.score >= 0.55, dup
    note = next(n for n in plan.notes if "NOT imported" in n)
    assert "L1" in note and f"{dup.score:.2f}" in note, note


def test_applying_writes_the_new_bullet_and_not_the_repeat(repo):
    _corpus(repo)
    run_cli(repo, "init")
    _apply(repo)
    state = fold(EventLog(repo).read_all())
    titles = " ".join(ls.title for ls in state.lessons.values())
    assert "Cache keys" in titles and "Rebase before merging" not in titles, titles


def test_re_importing_the_same_files_adds_nothing(repo):
    _corpus(repo)
    run_cli(repo, "init")
    _apply(repo)
    before = len(EventLog(repo).read_all())
    again = _plan(repo)
    assert not again.found, [f.ident for f in again.found]
    _apply(repo)
    assert len(EventLog(repo).read_all()) == before


def test_identical_text_under_another_id_is_not_imported_again(repo):
    _corpus(repo, summary=False)
    run_cli(repo, "init")
    log = EventLog(repo, agent_id="someone")
    log.append(
        "lesson.recorded",
        "L-earlier",
        {
            "title": "Never force a push to a shared branch",
            "rule": (
                "Force pushing rewrites history other worktrees are built on; use a "
                "revert commit instead."
            ),
        },
    )
    plan = _plan(repo)
    assert sorted(_lessons(plan)) == ["L1"], sorted(_lessons(plan))
    (dup,) = plan.duplicates
    assert (dup.found.ident, dup.of, dup.identical) == ("L2", "L-earlier", True), dup
    assert any("word for word" in n and "L2 = L-earlier" in n for n in plan.notes), plan.notes


def test_a_near_duplicate_of_a_queued_lesson_is_reported_with_its_candidate(repo):
    _corpus(repo, summary=False)
    run_cli(repo, "init")
    EventLog(repo, agent_id="someone").append(
        "lesson.recorded",
        "L-held",
        {
            "title": "Rebase agent branches onto main first",
            "rule": (
                "Always rebase an agent branch onto the current main before merging it, "
                "otherwise the merge silently reverts work another agent landed."
            ),
        },
    )
    plan = _plan(repo)
    assert "L1" not in _lessons(plan) and "L2" in _lessons(plan)
    (dup,) = plan.duplicates
    assert (dup.found.ident, dup.of, dup.where) == ("L1", "L-held", "queue"), dup
    assert 0.55 <= dup.score < 1.0, dup.score


def test_dedupe_off_imports_everything(repo):
    _corpus(repo)
    run_cli(repo, "init")
    cfg = repo / ".ddflow" / "config.toml"
    cfg.write_text(cfg.read_text() + '\n[dedupe]\non_match = "off"\n')
    plan = _plan(repo)
    assert not plan.duplicates
    assert sum(1 for i in _lessons(plan) if i.startswith("LS-")) == 2
