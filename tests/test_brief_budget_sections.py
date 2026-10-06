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
    others = EventLog(repo, "a2")  # the jobs are ANOTHER agent's, as in the report
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
        others.append(
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


def test_the_agents_own_jobs_are_listed_in_full(repo):
    """Only other agents' exited jobs collapse; the brief's own agent's are its to collect."""
    _project(repo)
    EventLog(repo, "a1").append(
        "job.started", "Jmine-elsewhere", {"item": "OTHER3", "command": "x", "pid": 0}
    )
    text = lifecycle.brief(repo, item="MINE", agent="a1").data["text"]
    assert "Jmine-elsewhere" in text and "Jother000" not in text


def test_the_section_shares_never_overrun_the_room():
    """Critic: floors taken before the shares pushed the total past the room, and then
    nothing was trimmed. Nine sections each at their floor-raised allowance, 80 over."""
    from ddflow.views import markdown as M

    sizes = dict(zip(M._SECTION_SHARE, (480, 240, 960, 576, 1200, 384, 160, 720, 160), strict=True))
    sections = [(n, ["x" * (sz - 1)]) for n, sz in sizes.items()]
    body, trimmed = M._fit_sections(sections, 4800, "MINE")
    assert trimmed
    assert len("\n".join(body)) <= 4800
    small = [(n, ["y" * 149]) for n in M._SECTION_SHARE]
    body, trimmed = M._fit_sections(small, 1000, "MINE")
    assert trimmed and len("\n".join(body)) <= 1000 + 9 * 160, "nine headings at most"


def test_hundreds_of_decisions_still_leave_rules_and_lessons(repo):
    """roborev: the marker named every left-out id, so 300 decisions made the decisions
    section overrun its share and the backstop cut the rules and lessons again."""
    _project(repo)
    log = EventLog(repo, "a1")
    for i in range(300):
        log.append(
            "decision.recorded",
            f"DX{i:03d}",
            {"title": f"extra {i}", "decision": "rotate by size " * 20, "globs": ["src/zebra/*"]},
        )
    data = lifecycle.brief(repo, item="MINE", agent="a1").data
    text = data["text"]
    assert data["approx_tokens"] <= 1200 * 1.05
    assert "## Project rules" in text and "## Lessons that bear on this task" in text
    assert "L-zebra" in text
    assert " more cut to fit" in text and "ddflow decision applicable MINE" in text
    # The marker names a few and counts the rest: naming all 300 overran the share.
    markers = [ln for ln in text.splitlines() if "cut to fit" in ln]
    assert all(len(ln) < 400 for ln in markers), max(map(len, markers))


def test_a_tiny_budget_is_still_a_budget(repo):
    """roborev: a budget under the note's length sliced from the END and kept the text."""
    from ddflow.api._base import _load
    from ddflow.core.schedule import plan
    from ddflow.views import markdown as M

    _project(repo)
    _log, cfg, st = _load(repo, "a1")
    cfg.session.brief_max_tokens = 30
    lessons = [{"id": "L-zebra", "title": "t", "rule": "r " * 200}]
    text = M.brief(st, cfg, plan(st, cfg), item="MINE", lessons=lessons, reserve=29)
    # budget 1 token: nothing but the truncation note survives
    assert text.startswith("\n\n_[brief truncated at 1 tokens"), text[:200]


def test_a_floor_holds_without_overrunning_the_room():
    """Critic: shares first left the small sections under their floor whenever the big
    ones wanted their whole share. Floors are met and the total still fits."""
    from ddflow.views import markdown as M

    sections = [(n, ["z" * 5000]) for n in M._SECTION_SHARE]
    sizes = [5001] * len(sections)
    allow = M._allowances(sections, sizes, 2000)
    assert sum(allow) <= 2000
    assert min(allow) >= M._SECTION_FLOOR
    # floors that cannot all fit: plain shares, still inside the room
    tiny = M._allowances(sections, sizes, 900)
    assert sum(tiny) <= 900


def test_the_agents_own_job_outranks_other_running_jobs(repo, monkeypatch):
    from ddflow.services import jobs as J
    from ddflow.views import markdown as M

    _project(repo)
    log = EventLog(repo, "a1")
    log.append("job.started", "Jzz-mine", {"item": "OTHER5", "command": "x", "pid": 0})
    from ddflow.core.model import fold

    st = fold(EventLog(repo, "a1").read_all(), strict=False)
    real = J.status
    monkeypatch.setattr(
        J, "status", lambda j: J.Status("running", "pid 1 running") if j.by == "a2" else real(j)
    )
    out: list[str] = []
    M._brief_jobs(out, st, "MINE", "a1")
    listed = [ln for ln in out if ln.startswith("- **")]
    assert "Jzz-mine" in listed[0], listed[:3]
