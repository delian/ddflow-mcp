"""B1472311a63: every section of the brief is budgeted, so none can crowd out the rest.

`ddflow brief --item X` was cut at `session.brief_max_tokens` from the bottom only: a
long section ahead of the decisions, rules and lessons -- other items' jobs, leftovers,
blocked items, or the decisions themselves (eight live ones, 7309 characters, against a
4800-character budget) -- took the whole budget, and the agent never saw the rules and
lessons the brief exists to give it.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.api import lifecycle
from ddflow.infra.log import EventLog

JOBS = 20
DECISIONS = 8


def _project(repo: Path) -> None:
    assert run_cli(repo, "init")[0] == 0
    (repo / "CLAUDE.md").write_text("# rules\n")
    log = EventLog(repo, "a1")
    log.append(
        "task.added",
        "MINE",
        {"title": "rotate the zebra cache", "kind": "task", "globs": ["src/zebra/*"]},
    )
    for i in range(JOBS):
        log.append("task.added", f"OTHER{i}", {"title": f"other {i}", "kind": "task"})
        # An exit line in its log and a dead pid: EXITED, and nobody recorded it ended.
        out = repo / f"job{i}.log"
        out.write_text("done\nddflow-job-exit: 0\n")
        log.append(
            "job.started",
            f"Jother{i:03d}",
            {"item": f"OTHER{i}", "command": "x", "pid": 0, "log": str(out)},
        )
    for i in range(DECISIONS):
        log.append(
            "decision.recorded",
            f"D{i}",
            {
                "title": f"decision {i} about the zebra cache",
                "decision": f"zebra rule {i}: " + "the cache is rotated by size, not age. " * 50,
                "globs": ["src/zebra/*"] if i % 2 else [],
            },
        )
    log.append(
        "lesson.recorded",
        "L-zebra",
        {
            "title": "rotate the zebra cache only after a flush",
            "rule": "flush the zebra cache before you rotate it",
            "why": "a rotation without a flush lost writes",
        },
    )


def test_a_brief_with_twenty_exited_jobs_still_shows_rules_and_lessons(repo):
    _project(repo)
    data = lifecycle.brief(repo, item="MINE", agent="a1").data
    text = data["text"]
    assert "## Current: MINE" in text or "## Suggested next: MINE" in text, text[:800]
    assert "## Architectural decisions governing these files" in text
    assert "## Project rules" in text, text[-1200:]
    assert "## Lessons that bear on this task" in text, text[-1200:]
    assert "L-zebra" in text
    # Still inside the budget: the sections were trimmed, not the budget raised.
    assert data["approx_tokens"] <= 1200 * 1.05


def test_trimmed_sections_say_where_the_rest_is(repo):
    _project(repo)
    text = lifecycle.brief(repo, item="MINE", agent="a1").data["text"]
    assert f"{JOBS} more unended job(s)" in text and "ddflow job list" in text
    assert "Jother000" not in text, "another item's exited job is listed in full"
    # A decision the budget could not show is still NAMED, with where to read it.
    shown = [f"D{i}" for i in range(DECISIONS) if f'id="D{i}"' in text]
    assert shown, "no decision reached the brief"
    if len(shown) < DECISIONS:
        assert "ddflow decision applicable MINE" in text
        for i in range(DECISIONS):
            assert f"D{i}" in text, f"decision D{i} is neither shown nor named"


def test_a_brief_that_fits_is_not_trimmed(repo):
    from ddflow.views import markdown as M

    assert run_cli(repo, "init")[0] == 0
    EventLog(repo, "a1").append("task.added", "T", {"title": "small", "kind": "task"})
    text = lifecycle.brief(repo, item="T", agent="a1").data["text"]
    assert "brief truncated" not in text
    assert M.SECTION_TRIMMED not in text
