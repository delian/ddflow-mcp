"""`complete` closes only the bugs the fix task was filed to fix (B7bdcc6b212).

2026-10-04: completing fix-B297ede2447 closed B297ede2447 and also four bugs merely
filed with `--item fix-B297ede2447` -- three scope=ddflow upstream reports and a test
flake with a fix task of its own. The sweep asked "whose `fix_task` is this item", and
`bug found --item <open fix task>` links a report to the task it was filed against. A
completion now closes the task's `fixes` (and the bug a hand-filed `fix-<bug>` names),
never a bug reported against it; and a wrongly closed bug can be reopened.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import pass_pipeline, run_cli
from helpers import state_as_reader as state

from ddflow.api import knowledge as K
from ddflow.api import lifecycle as LC
from ddflow.api.bug_reopen import bug_reopen
from ddflow.core.model import HANDLERS, fold
from ddflow.infra.log import EventLog
from ddflow.services import verify as SV


def seed(repo: Path) -> str:
    """A done task T1 with bug Bx found on it (so `fix-Bx` is filed), and a regression
    test file that exists."""
    run_cli(repo, "init")
    log = EventLog(repo, "seed")
    log.append("phase.added", "P1", {"title": "Phase one"})
    log.append("task.added", "T1", {"parent": "P1", "title": "parser", "globs": ["src/p.py"]})
    log.append("item.completed", "T1", {"sha": "abc"})
    K.bug_found(repo, summary="parser drops the last line", item="T1", id="Bx", agent="a")
    t = repo / "tests" / "test_p_regress.py"
    t.parent.mkdir(exist_ok=True)
    t.write_text("def test_last_line():\n    pass\n")
    return "tests/test_p_regress.py::test_last_line"


def test_a_bug_reported_against_the_fix_task_is_not_closed_by_its_completion(repo):
    node = seed(repo)
    # Both are filed against the open fix task, so `bug found` links them to it.
    K.bug_found(repo, summary="reviewer took the MCP down", item="fix-Bx", id="Bup",
                scope="ddflow", agent="a")  # fmt: skip
    K.bug_found(repo, summary="kill-wait test flakes", item="fix-Bx", id="Bflake", agent="a")
    st = state(repo)
    assert st.bugs["Bup"].fix_task == "fix-Bx" and st.bugs["Bflake"].fix_task == "fix-Bx"
    pass_pipeline(repo, "fix-Bx")
    out = LC.complete(repo, "fix-Bx", model="claude-opus-5", regression_test=node, agent="a")
    assert out.exit == 0, out.reason
    assert out.data["bugs_closed"] == ["Bx"], out.data
    st = state(repo)
    assert st.bugs["Bx"].resolution == "fixed"
    assert st.bugs["Bup"].open and st.bugs["Bflake"].open
    # Said out loud: the reports stay open and are not this task's to close.
    assert any("Bflake" in w and "Bup" in w for w in out.data["warnings"]), out.data
    # ...and each gets a fix task of its own, rather than pointing at a finished task
    # that `bug file-tasks` would never refile (roborev job 1426 #1).
    assert out.data["bugs_refiled"] == {"Bflake": "fix-Bflake", "Bup": "fix-Bup"}, out.data
    st = state(repo)
    assert st.bugs["Bup"].fix_task == "fix-Bup" and st.items["fix-Bup"].fixes == ["Bup"]
    assert st.items["fix-Bup"].state == "open" and st.items["fix-Bup"].parent == "P1"
    assert K.bug_file_tasks(repo, agent="a").exit == 2, "nothing left without a fix task"


def test_a_reported_bug_does_not_block_the_fix_tasks_completion(repo):
    seed(repo)
    K.bug_found(repo, summary="kill-wait test flakes", item="fix-Bx", id="Bflake", agent="a")
    pass_pipeline(repo, "fix-Bx")
    out = LC.complete(repo, "fix-Bx", model="claude-opus-5", agent="a")
    blockers = [b for b in out.data["blockers"] if b.startswith("fixes open bug(s) ")]
    assert len(blockers) == 1 and "Bx" in blockers[0] and "Bflake" not in blockers[0]


def test_a_bug_with_its_own_fix_task_is_closed_by_that_task_only(repo):
    """B90101e389c: linked to fix-B297ede2447 by its report, with fix-B90101e389c filed
    for it by hand (`fixes` = [it])."""
    node = seed(repo)
    K.bug_found(repo, summary="kill-wait test flakes", item="fix-Bx", id="Bflake", agent="a")
    EventLog(repo, "seed").append(
        "task.added",
        "fix-Bflake",
        {"parent": "P1", "title": "Fix bug Bflake", "tags": ["bugfix"], "fixes": ["Bflake"]},
    )
    pass_pipeline(repo, "fix-Bx")
    assert LC.complete(repo, "fix-Bx", model="claude-opus-5", regression_test=node,
                       agent="a").exit == 0  # fmt: skip
    st = state(repo)
    assert st.bugs["Bflake"].open and st.bugs["Bflake"].fix_task == "fix-Bflake"
    pass_pipeline(repo, "fix-Bflake")
    out = LC.complete(repo, "fix-Bflake", model="claude-opus-5", regression_test=node, agent="a")
    assert out.exit == 0 and out.data["bugs_closed"] == ["Bflake"], out.reason


def test_verify_does_not_hold_a_reported_bug_against_the_completed_task(repo):
    node = seed(repo)
    K.bug_found(repo, summary="kill-wait test flakes", item="fix-Bx", id="Bflake", agent="a")
    pass_pipeline(repo, "fix-Bx")
    assert LC.complete(repo, "fix-Bx", model="claude-opus-5", regression_test=node,
                       agent="a").exit == 0  # fmt: skip
    claim = SV._regression(state(repo), "fix-Bx", None)
    assert claim is not None and claim.status == SV.OK, claim


# -- reopen ----------------------------------------------------------------------------


def test_reopen_is_registered():
    assert "bug.reopened" in HANDLERS


def test_a_wrongly_closed_bug_can_be_reopened_and_closed_again(repo):
    node = seed(repo)
    assert K.bug_fixed(repo, "Bx", regression_test=[node], agent="a").exit == 0
    out = bug_reopen(repo, "Bx", reason="closed by another task's completion", agent="a")
    assert out.exit == 0, out.reason
    assert out.data["was"] == "fixed" and out.data["fix_task"] == "fix-Bx", out.data
    b = state(repo).bugs["Bx"]
    assert b.open and not b.regression_test and not b.regression_tests
    assert b.reopen_reason == "closed by another task's completion"
    # Closed again the ordinary way.
    assert K.bug_fixed(repo, "Bx", regression_test=[node], agent="a").exit == 0
    assert state(repo).bugs["Bx"].resolution == "fixed"


def test_reopen_points_the_bug_at_its_own_open_fix_task(repo):
    """The linked task that closed it is done; its own `fix-<bug>` is the fix now."""
    node = seed(repo)
    K.bug_found(repo, summary="kill-wait test flakes", item="fix-Bx", id="Bflake", agent="a")
    log = EventLog(repo, "seed")
    log.append("bug.fixed", "Bflake", {"regression_test": node})  # the old sweep
    log.append("task.added", "fix-Bflake",
               {"parent": "P1", "title": "Fix bug Bflake", "fixes": ["Bflake"]})  # fmt: skip
    log.append("item.completed", "fix-Bx", {"sha": "abc"})
    out = bug_reopen(repo, "Bflake", reason="not fixed by fix-Bx", agent="a")
    assert out.exit == 0 and out.data["fix_task"] == "fix-Bflake", out.data
    assert state(repo).bugs["Bflake"].fix_task == "fix-Bflake"


def test_reopen_unlinks_a_done_task_that_never_fixed_it_so_file_tasks_files_one(repo):
    node = seed(repo)
    K.bug_found(repo, summary="reviewer took the MCP down", item="fix-Bx", id="Bup", agent="a")
    log = EventLog(repo, "seed")
    log.append("bug.fixed", "Bup", {"regression_test": node})  # the old sweep
    log.append("item.completed", "fix-Bx", {"sha": "abc"})
    out = bug_reopen(repo, "Bup", reason="not fixed by fix-Bx", agent="a")
    assert out.exit == 0 and out.data["fix_task"] == "", out.data
    assert state(repo).bugs["Bup"].fix_task == ""
    filed = K.bug_file_tasks(repo, agent="a")
    assert filed.data["filed"] == ["Bup"], filed.data
    assert state(repo).items["fix-Bup"].fixes == ["Bup"]


def test_reopen_refuses_an_open_bug_an_unknown_one_and_no_reason(repo):
    seed(repo)
    assert bug_reopen(repo, "Bx", reason="r", agent="a").exit == 3
    assert bug_reopen(repo, "Bnope", reason="r", agent="a").exit == 3
    assert bug_reopen(repo, "Bx", reason="  ", agent="a").exit == 1


def test_reopen_on_the_cli(repo):
    seed(repo)
    run_cli(repo, "bug", "invalid", "Bx", "--reason", "looked false")
    code, out, err = run_cli(repo, "--json", "bug", "reopen", "Bx", "--reason", "it is real")
    assert code == 0, err
    body = json.loads(out)
    assert body["id"] == "Bx" and body["was"] == "invalid", body
    b = state(repo).bugs["Bx"]
    assert b.open and not b.invalid_reason
    code, _, err = run_cli(repo, "bug", "reopen", "Bx", "--reason", "again")
    assert code == 3 and "open" in err


def test_reopen_leaves_no_closure_field_behind(repo):
    """roborev job 1426 #4: every field a closure writes reads at its default again."""
    from dataclasses import fields

    from ddflow.core.events import Event
    from ddflow.core.model import Bug

    found = Event(kind="bug.found", subject="B1", data={"summary": "s"}, ts="t0")
    blank = fold([found]).bugs["B1"]
    closures = [
        Event(kind="bug.fixed", subject="B1", ts="t1", data={
            "regression_test": "t::x", "regression_tests": ["t::x"], "lesson": "L1",
            "changelog": {"category": "Fixed", "line": "x"}}),
        Event(kind="bug.invalid", subject="B1", ts="t2", data={"reason": "r", "evidence": "e"}),
    ]  # fmt: skip
    reopen = Event(kind="bug.reopened", subject="B1", ts="t3", data={"reason": "why"})
    b = fold([found, *closures, reopen]).bugs["B1"]
    for f in fields(Bug):
        if f.name in ("reopened_at", "reopen_reason"):
            continue
        assert getattr(b, f.name) == getattr(blank, f.name), f.name
    assert b.open and b.reopen_reason == "why"


def test_reopening_a_bug_whose_own_fix_did_not_hold_keeps_its_task_for_verify(repo):
    """bug_hunt probe: unlinking the bug's own DONE fix task left `bug file-tasks` to
    link it straight back (a live `fix-<bug>`), so the record kept pointing at a finished
    task. The done task is kept, and `verify --reopen` sends it back to the queue."""
    node = seed(repo)
    pass_pipeline(repo, "fix-Bx")
    assert LC.complete(repo, "fix-Bx", model="claude-opus-5", regression_test=node,
                       agent="a").exit == 0  # fmt: skip
    out = bug_reopen(repo, "Bx", reason="the fix did not hold", agent="a")
    assert out.exit == 0, out.reason
    assert (out.data["fix_task"], out.data["fix_task_state"]) == ("fix-Bx", "done"), out.data
    code, _, err = run_cli(repo, "verify", "fix-Bx", "--reopen")
    assert code == 0, err
    st = state(repo)
    assert st.items["fix-Bx"].state == "open" and st.bugs["Bx"].fix_task == "fix-Bx"


def test_with_file_task_off_the_reports_stay_open_and_the_warning_says_no_task(repo):
    """roborev job 1428 #2: `[bugs] file_task = false` files nothing, and says so."""
    node = seed(repo)
    K.bug_found(repo, summary="kill-wait test flakes", item="fix-Bx", id="Bflake", agent="a")
    cfg = repo / ".ddflow" / "config.toml"
    cfg.write_text(cfg.read_text() + "\n[bugs]\nfile_task = false\n")
    pass_pipeline(repo, "fix-Bx")
    out = LC.complete(repo, "fix-Bx", model="claude-opus-5", regression_test=node, agent="a")
    assert out.exit == 0 and out.data["bugs_refiled"] == {}, out.data
    assert any("Bflake" in w and "files no fix task" in w for w in out.data["warnings"])
    assert state(repo).bugs["Bflake"].open and "fix-Bflake" not in state(repo).items


def test_the_reports_get_their_tasks_before_the_completion_is_written(repo):
    """roborev job 1428 #1: a crash after the refile and before `item.completed` must not
    strand the reports on a done task -- so the refile is written first."""
    node = seed(repo)
    K.bug_found(repo, summary="kill-wait test flakes", item="fix-Bx", id="Bflake", agent="a")
    pass_pipeline(repo, "fix-Bx")
    assert LC.complete(repo, "fix-Bx", model="claude-opus-5", regression_test=node,
                       agent="a").exit == 0  # fmt: skip
    kinds = [(e.kind, e.subject) for e in EventLog(repo, "r").read_all()]
    refile = kinds.index(("task.added", "fix-Bflake"))
    assert refile < kinds.index(("item.completed", "fix-Bx")), kinds[refile - 3 :]


def test_reopen_names_an_abandoned_fix_task_rather_than_saying_none_was_filed(repo):
    """roborev job 1432 #4: an abandoned `fix-<bug>` is named, not reported as none."""
    node = seed(repo)
    log = EventLog(repo, "seed")
    log.append("bug.invalid", "Bx", {"reason": "looked false", "evidence": ""})
    log.append("item.abandoned", "fix-Bx", {"reason": "bug was invalid"})
    out = bug_reopen(repo, "Bx", reason="it is real after all", agent="a")
    assert (out.data["fix_task"], out.data["fix_task_state"]) == ("fix-Bx", "abandoned")
    code, text, err = run_cli(repo, "bug", "reopen", "Bx", "--reason", "x")
    assert code == 3  # open now
    log.append("bug.invalid", "Bx", {"reason": "again", "evidence": ""})
    code, text, err = run_cli(repo, "bug", "reopen", "Bx", "--reason", "real")
    assert code == 0, err
    assert "fix task fix-Bx was abandoned (nothing revives it)" in text, text
    assert "`ddflow bug file-tasks` files it a new one" in text, text
    assert "`ddflow bug fixed Bx --regression-test <test>` closes it" in text, text
    # ...and that way out works: the bug closes with its regression test.
    assert K.bug_fixed(repo, "Bx", regression_test=[node], agent="a").exit == 0


def test_reopen_names_a_done_fix_before_an_abandoned_one(repo):
    """roborev job 1433 #3: both filed to fix it -- the one that landed is named."""
    seed(repo)
    log = EventLog(repo, "seed")
    log.append("task.added", "B-fix-2", {"parent": "P1", "title": "fix 2", "fixes": ["Bx"]})
    log.append("bug.found", "Bx", {"fix_task": "B-fix-2"})
    log.append("item.abandoned", "B-fix-2", {"reason": "superseded"})
    log.append("item.completed", "fix-Bx", {"sha": "abc"})
    log.append("bug.fixed", "Bx", {"regression_test": "t::x"})
    out = bug_reopen(repo, "Bx", reason="did not hold", agent="a")
    assert (out.data["fix_task"], out.data["fix_task_state"]) == ("fix-Bx", "done"), out.data
    assert "verify fix-Bx --reopen" in out.data["next"]
