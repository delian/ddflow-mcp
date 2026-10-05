"""B8114a8b531: other items' stale jobs must not crowd an item's own brief out.

`ddflow brief --item X` in a project with dozens of unended (exited or killed) jobs of
OTHER items printed one line per job ahead of X's own section and was cut at
`session.brief_max_tokens` before X, its decisions, rules and lessons ever appeared.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.api import lifecycle
from ddflow.infra.log import EventLog

STALE = 40


def _project(repo: Path) -> None:
    assert run_cli(repo, "init")[0] == 0
    log = EventLog(repo, "a1")
    log.append("task.added", "MINE", {"title": "the item asked about", "kind": "task"})
    for i in range(STALE):
        log.append("task.added", f"OTHER{i}", {"title": f"other {i}", "kind": "task"})
        # pid 0 with no log: never running, never ended -- a job nobody collected.
        log.append("job.started", f"Jother{i:03d}", {"item": f"OTHER{i}", "command": "x", "pid": 0})
    log.append("job.started", "Jmine", {"item": "MINE", "command": "train", "pid": 0})


def test_brief_for_an_item_reaches_its_own_section(repo):
    _project(repo)
    text = lifecycle.brief(repo, item="MINE", agent="a1").data["text"]
    assert "## Current: MINE" in text or "## Suggested next: MINE" in text, text[-600:]
    assert "Jmine" in text, "the item's own job must still be listed"
    assert "Jother000" not in text, "another item's stale job is listed in full"
    assert f"{STALE} more unended job(s)" in text and "ddflow job list" in text


def test_brief_without_an_item_lists_a_bounded_number_of_stale_jobs(repo):
    _project(repo)
    text = lifecycle.brief(repo, agent="a1").data["text"]
    listed = [ln for ln in text.splitlines() if ln.startswith("- **") and "`J" in ln]
    assert 0 < len(listed) <= 5, listed
    assert "more unended job(s)" in text and "ddflow job list" in text
    assert "brief truncated" not in text, text[-400:]
