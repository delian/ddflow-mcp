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
    # Other AGENTS' jobs: the brief's own agent's are listed in full (B1472311a63).
    others = EventLog(repo, "a2")
    log.append("task.added", "MINE", {"title": "the item asked about", "kind": "task"})
    for i in range(STALE):
        log.append("task.added", f"OTHER{i}", {"title": f"other {i}", "kind": "task"})
        # pid 0 with no log: never running, never ended -- a job nobody collected.
        others.append(
            "job.started", f"Jother{i:03d}", {"item": f"OTHER{i}", "command": "x", "pid": 0}
        )
    log.append("job.started", "Jmine", {"item": "MINE", "command": "train", "pid": 0})


def test_brief_for_an_item_reaches_its_own_section(repo):
    _project(repo)
    text = lifecycle.brief(repo, item="MINE", agent="a1").data["text"]
    assert "## Current: MINE" in text or "## Suggested next: MINE" in text, text[-600:]
    assert "Jmine" in text, "the item's own job must still be listed"
    assert "Jother000" not in text, "another item's stale job is listed in full"
    assert f"{STALE} more unended job(s)" in text and "ddflow job list" in text


def _stale_state(repo: Path, n: int):
    from ddflow.core.model import fold

    log = EventLog(repo, "a1")
    for i in range(n):
        log.append("job.started", f"Jold{i:03d}", {"item": f"OTHER{i}", "command": "x", "pid": 0})
    return fold(log.read_all(), strict=False)


def test_a_brief_about_no_item_lists_the_newest_five(repo):
    """The no-item branch, driven directly: `lifecycle.brief` picks the held or top ready
    item whenever there is one, so through it this branch runs only on an empty queue."""
    from ddflow.views import markdown as M

    out: list[str] = []
    M._brief_jobs(out, _stale_state(repo, STALE), "")
    listed = [ln for ln in out if ln.startswith("- **")]
    assert [ln.split("`")[1] for ln in listed] == [f"Jold{i:03d}" for i in range(35, 40)]
    assert f"{STALE - 5} more unended job(s)" in "\n".join(out)


def test_a_job_on_another_host_is_listed_not_collapsed_as_dead(repo, monkeypatch):
    from ddflow.services import jobs as J
    from ddflow.views import markdown as M

    st = _stale_state(repo, 3)
    st.jobs["Jold001"].host = "some-other-host"
    monkeypatch.setattr(J, "host", lambda: "this-host")
    out: list[str] = []
    M._brief_jobs(out, st, "MINE")
    text = "\n".join(out)
    assert "Jold001" in text and "ELSEWHERE" in text
    assert "Jold000" not in text and "2 more unended job(s)" in text


def test_jobs_on_other_hosts_are_bounded_too(repo, monkeypatch):
    """A job on another host never resolves here, so a backlog of them would crowd the
    brief out exactly as local stale jobs did."""
    from ddflow.services import jobs as J
    from ddflow.views import markdown as M

    st = _stale_state(repo, STALE)
    for j in st.jobs.values():
        j.host = "some-other-host"
    monkeypatch.setattr(J, "host", lambda: "this-host")
    out: list[str] = []
    M._brief_jobs(out, st, "MINE")
    listed = [ln for ln in out if ln.startswith("- **")]
    assert [ln.split("`")[1] for ln in listed] == [f"Jold{i:03d}" for i in range(35, 40)]
    assert f"{STALE - 5} more job(s) of other items on other hosts" in "\n".join(out)


def test_the_items_own_job_survives_a_flood_of_the_agents_own(repo):
    """B1472311a63: the agent's own jobs are listed in full, so forty of them fill the jobs
    section -- which is then cut to its share, the item's own job first."""
    _project(repo)
    log = EventLog(repo, "a1")
    for i in range(STALE):
        log.append("job.started", f"Jown{i:03d}", {"item": f"OTHER{i}", "command": "x", "pid": 0})
    text = lifecycle.brief(repo, item="MINE", agent="a1").data["text"]
    assert "Jmine" in text
    assert "## Current: MINE" in text or "## Suggested next: MINE" in text
