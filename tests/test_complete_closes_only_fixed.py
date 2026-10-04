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

from ddflow.api import knowledge as K
from ddflow.api import lifecycle as LC
from ddflow.api.bug_reopen import bug_reopen
from ddflow.core.model import HANDLERS, fold
from ddflow.infra.log import EventLog
from ddflow.services import verify as SV


def state(repo: Path):
    return fold(EventLog(repo, "reader").read_all(), strict=False)


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
    assert state(repo).bugs["Bflake"].open
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
