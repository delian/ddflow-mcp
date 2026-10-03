"""Bugs are queue items (B-bugs-as-items).

Operator 2026-09-28: "Can the bugs be treated as tasks and part of the DAG tree with
priority for correction by default in order to not accumulate bugs over bugs?"

`bug found` files the fix task by default (`[bugs].file_task`), under the phase of the
item the bug names or a standing `bugs` phase, tagged as a bug fix and carrying the item's
globs -- so `bugs_first` offers it ahead of features and a feature on the same files waits
behind it. The bug closes when that task completes with a verified regression test:
`complete` refuses while a bug the task fixes is open, and `--regression-test` closes it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import pass_pipeline, run_cli

from ddflow.api import knowledge as K
from ddflow.api import lifecycle as LC
from ddflow.config import Config
from ddflow.core.model import fold
from ddflow.core.schedule import plan
from ddflow.infra.log import EventLog
from ddflow.surfaces.mcp import Server


def state(repo: Path):
    return fold(EventLog(repo, "reader").read_all(), strict=False)


def seed(repo: Path, *, feature: bool = True):
    """A phase with a done task T1 (where the bug is found) and, optionally, a feature F1
    on the same files at the default priority."""
    run_cli(repo, "init")
    log = EventLog(repo, "seed")
    log.append("phase.added", "P1", {"title": "Phase one"})
    log.append("task.added", "T1", {"parent": "P1", "title": "parser", "globs": ["src/parser.py"]})
    log.append("item.completed", "T1", {"sha": "abc"})
    if feature:
        log.append(
            "task.added",
            "F1",
            {"parent": "P1", "title": "feature on the parser", "globs": ["src/parser.py"]},
        )
    return log


# -- filing ---------------------------------------------------------------------------


def test_bug_found_files_a_fix_task_under_the_items_phase_with_its_globs(repo):
    seed(repo)
    out = K.bug_found(repo, summary="parser drops the last line", item="T1", id="Bx", agent="a")
    assert out.exit == 0, out.reason
    assert out.data["fix_task"] == "fix-Bx" and out.data["fix_task_filed"] is True
    st = state(repo)
    fix = st.items["fix-Bx"]
    assert fix.kind == "task" and fix.parent == "P1"
    assert fix.globs == ["src/parser.py"]
    assert "bugfix" in fix.tags
    assert fix.fixes == ["Bx"]
    assert "Bx" in fix.title and "parser drops the last line" in fix.title
    assert st.bugs["Bx"].fix_task == "fix-Bx"


def test_the_fix_task_is_offered_before_a_same_priority_feature_on_the_same_files(repo, cfg):
    seed(repo)
    K.bug_found(repo, summary="parser drops the last line", item="T1", id="Bx", agent="a")
    p = plan(state(repo), cfg, phase="P1")
    assert [i.id for i in p.ready] == ["fix-Bx"]
    # the feature touches the same files: it waits behind the fix (no bugs over bugs)
    held = {b.item: b for b in p.blocked}
    assert "F1" in held and "fix-Bx" in held["F1"].detail


def test_show_lists_the_fix_task_on_the_bug_and_the_bug_on_the_task(repo):
    seed(repo)
    K.bug_found(repo, summary="parser drops the last line", item="T1", id="Bx", agent="a")
    code, out, _ = run_cli(repo, "--json", "show", "Bx")
    assert code == 0 and json.loads(out)["fixing"] == ["fix-Bx"]
    code, out, _ = run_cli(repo, "--json", "show", "fix-Bx")
    body = json.loads(out)
    assert code == 0 and body.get("item", body)["fixes"] == ["Bx"]


def test_no_task_files_only_the_bug(repo):
    seed(repo)
    out = K.bug_found(repo, summary="off by one", item="T1", id="Bx", no_task=True, agent="a")
    assert out.exit == 0 and out.data["fix_task"] == ""
    st = state(repo)
    assert "fix-Bx" not in st.items and st.bugs["Bx"].fix_task == ""


def test_the_knob_turns_filing_off(repo, monkeypatch):
    seed(repo)
    monkeypatch.setenv("DDFLOW_BUGS_FILE_TASK", "false")
    out = K.bug_found(repo, summary="off by one", item="T1", id="Bx", agent="a")
    assert out.exit == 0 and out.data["fix_task"] == ""
    assert "fix-Bx" not in state(repo).items


def test_a_bug_naming_no_item_goes_under_the_standing_bugs_phase_made_once(repo):
    seed(repo)
    a = K.bug_found(repo, summary="first stray bug", id="B1", agent="a")
    b = K.bug_found(repo, summary="second stray bug", id="B2", agent="a")
    assert a.exit == 0 and b.exit == 0
    st = state(repo)
    assert st.items["bugs"].kind == "phase"
    assert st.items["fix-B1"].parent == "bugs" and st.items["fix-B2"].parent == "bugs"
    assert st.items["fix-B1"].globs == []
    assert sum(1 for e in EventLog(repo, "r").read_all() if e.subject == "bugs") == 1


def test_a_bug_in_a_finished_phase_goes_under_the_bugs_phase_with_the_items_globs(repo):
    log = seed(repo, feature=False)
    log.append("item.completed", "P1", {"sha": "abc"})
    out = K.bug_found(repo, summary="late finding", item="T1", id="Bx", agent="a")
    assert out.exit == 0
    fix = state(repo).items["fix-Bx"]
    assert fix.parent == "bugs" and fix.globs == ["src/parser.py"]


def test_explicit_globs_override_the_inherited_ones(repo):
    seed(repo)
    out = K.bug_found(
        repo, summary="late finding", item="T1", id="Bx", globs="src/lexer.py", agent="a"
    )
    assert out.exit == 0
    assert state(repo).items["fix-Bx"].globs == ["src/lexer.py"]


def test_an_open_bugfix_task_named_as_the_item_is_the_fix_and_nothing_new_is_filed(repo):
    seed(repo)
    EventLog(repo, "seed").append(
        "task.added", "B-fix-it", {"parent": "P1", "title": "fix it", "tags": ["bug"]}
    )
    out = K.bug_found(repo, summary="found while fixing", item="B-fix-it", id="Bx", agent="a")
    assert out.exit == 0
    assert out.data["fix_task"] == "B-fix-it" and out.data["fix_task_filed"] is False
    st = state(repo)
    assert st.bugs["Bx"].fix_task == "B-fix-it" and "fix-Bx" not in st.items


def test_an_open_untagged_task_named_as_the_item_is_where_it_was_found_not_its_fix(repo):
    """rubber_duck #1 (refuted by this probe): only a task `branch_kind` calls a bug fix
    -- one of `[flow] bugfix_tags`/`hotfix_tags` -- is taken as the fix; an ordinary open
    task named as the item is where the bug was found, and gets a fix task of its own."""
    seed(repo)
    out = K.bug_found(repo, summary="found while building", item="F1", id="Bx", agent="a")
    assert out.exit == 0
    assert out.data["fix_task"] == "fix-Bx" and out.data["fix_task_filed"] is True
    st = state(repo)
    assert st.items["fix-Bx"].parent == "P1" and st.items["fix-Bx"].globs == ["src/parser.py"]


def test_cli_reply_names_the_fix_task(repo):
    seed(repo)
    code, out, err = run_cli(
        repo,
        "bug",
        "found",
        "--summary",
        "parser drops the last line",
        "--item",
        "T1",
        "--id",
        "Bx",
    )
    assert code == 0, err
    assert "fix-Bx" in out
    code, out, _ = run_cli(repo, "--json", "bug", "found", "--summary", "another", "--id", "By")
    assert code == 0 and json.loads(out)["fix_task"] == "fix-By"
    code, out, _ = run_cli(
        repo, "--json", "bug", "found", "--summary", "third", "--id", "Bz", "--no-task"
    )
    assert code == 0 and json.loads(out)["fix_task"] == ""


def test_mcp_bug_found_returns_the_fix_task_and_takes_no_task(repo):
    seed(repo)
    srv = Server(repo)

    def call(args):
        return srv.handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "ddflow_bug_found", "arguments": args},
            }
        )["result"]

    r = call({"summary": "parser drops the last line", "item": "T1", "id": "Bx"})
    body = json.loads(r["content"][0]["text"])
    assert body["fix_task"] == "fix-Bx", body
    r = call({"summary": "same commit fix", "id": "By", "no_task": True})
    body = json.loads(r["content"][0]["text"])
    assert body["fix_task"] == "", body


# -- closing ----------------------------------------------------------------------------


def _fixed(repo: Path, bug: str = "Bx") -> str:
    """A regression test file that exists in the repository, as `bug fixed` requires."""
    t = repo / "tests" / "test_parser_regress.py"
    t.parent.mkdir(exist_ok=True)
    t.write_text("def test_last_line():\n    pass\n")
    return "tests/test_parser_regress.py::test_last_line"


def test_complete_refuses_the_fix_task_while_its_bug_is_open(repo):
    seed(repo)
    K.bug_found(repo, summary="parser drops the last line", item="T1", id="Bx", agent="a")
    pass_pipeline(repo, "fix-Bx")
    out = LC.complete(repo, "fix-Bx", model="claude-opus-5", agent="a")
    assert out.exit == 3, out.reason
    assert any("Bx" in b and "regression" in b for b in out.data["blockers"]), out.data


def test_complete_with_a_regression_test_closes_the_bug_and_completes(repo):
    seed(repo)
    K.bug_found(repo, summary="parser drops the last line", item="T1", id="Bx", agent="a")
    node = _fixed(repo)
    pass_pipeline(repo, "fix-Bx")
    code, out, err = run_cli(
        repo, "--json", "complete", "fix-Bx", "--model", "claude-opus-5", "--regression-test", node
    )
    assert code == 0, err
    assert json.loads(out)["bugs_closed"] == ["Bx"]
    st = state(repo)
    assert st.bugs["Bx"].resolution == "fixed" and st.bugs["Bx"].regression_test == node
    assert st.items["fix-Bx"].state == "done"


def test_a_refused_completion_closes_no_bug(repo):
    """critic #1 / rubber_duck #2: the bug closes when the task COMPLETES. A completion
    refused for any other reason (here: a pipeline never run) leaves the bug open."""
    seed(repo)
    K.bug_found(repo, summary="parser drops the last line", item="T1", id="Bx", agent="a")
    node = _fixed(repo)
    out = LC.complete(repo, "fix-Bx", model="claude-opus-5", regression_test=node, agent="a")
    assert out.exit == 3, out.reason
    assert not any("Bx" in b for b in out.data["blockers"]), "the open-bug blocker is lifted"
    assert out.data["bugs_closed"] == []
    st = state(repo)
    assert st.bugs["Bx"].open and st.items["fix-Bx"].state != "done"


def test_the_flag_on_an_item_that_fixes_no_open_bug_is_refused_not_dropped(repo):
    """roborev job 1296 #6: a regression test nobody records must not look recorded."""
    seed(repo)
    pass_pipeline(repo, "F1")
    out = LC.complete(
        repo, "F1", model="claude-opus-5", regression_test="tests/x.py::test_y", agent="a"
    )
    assert out.exit == 3 and "not the fix task of any open bug" in out.reason
    assert state(repo).items["F1"].state != "done"


def test_complete_refuses_a_regression_test_that_exists_nowhere(repo):
    seed(repo)
    K.bug_found(repo, summary="parser drops the last line", item="T1", id="Bx", agent="a")
    pass_pipeline(repo, "fix-Bx")
    out = LC.complete(
        repo, "fix-Bx", model="claude-opus-5", regression_test="tests/nope.py::test_x", agent="a"
    )
    assert out.exit != 0 and "nope" in out.reason
    st = state(repo)
    assert st.bugs["Bx"].open and st.items["fix-Bx"].state != "done"


def test_a_bug_closed_first_lets_the_task_complete_without_the_flag(repo):
    seed(repo)
    K.bug_found(repo, summary="parser drops the last line", item="T1", id="Bx", agent="a")
    node = _fixed(repo)
    assert K.bug_fixed(repo, "Bx", regression_test=node, agent="a").exit == 0
    pass_pipeline(repo, "fix-Bx")
    out = LC.complete(repo, "fix-Bx", model="claude-opus-5", agent="a")
    assert out.exit == 0, out.reason


def test_bug_invalid_removes_an_unclaimed_fix_task(repo):
    seed(repo)
    K.bug_found(repo, summary="parser drops the last line", item="T1", id="Bx", agent="a")
    out = K.bug_invalid(repo, "Bx", reason="the line is a trailer, by design", agent="a")
    assert out.exit == 0, out.reason
    assert out.data["fix_task_removed"] == "fix-Bx"
    assert state(repo).items["fix-Bx"].removed


def test_bug_invalid_keeps_a_fix_task_somebody_holds_and_names_it(repo):
    seed(repo)
    K.bug_found(repo, summary="parser drops the last line", item="T1", id="Bx", agent="a")
    code, _, err = run_cli(repo, "claim", "fix-Bx", "--no-worktree", agent="fixer")
    assert code == 0, err
    out = K.bug_invalid(repo, "Bx", reason="by design", agent="a")
    assert out.exit == 0 and out.data["fix_task_removed"] == ""
    assert out.data["fix_task"] == "fix-Bx", "rubber_duck #3: the kept task is named"
    assert not state(repo).items["fix-Bx"].removed


def test_bug_invalid_keeps_a_fix_task_something_depends_on(repo):
    """roborev job 1296 #2: the guards `remove` applies -- dependents, children -- hold
    here too, or a false finding strands the work filed on its fix."""
    seed(repo)
    K.bug_found(repo, summary="parser drops the last line", item="T1", id="Bx", agent="a")
    EventLog(repo, "seed").append(
        "task.added", "AFTER", {"parent": "P1", "title": "after the fix", "needs": ["fix-Bx"]}
    )
    out = K.bug_invalid(repo, "Bx", reason="by design", agent="a")
    assert out.exit == 0 and out.data["fix_task_removed"] == ""
    assert not state(repo).items["fix-Bx"].removed


# -- upgrade ----------------------------------------------------------------------------


def test_file_tasks_gives_every_open_bug_without_one_a_fix_task_and_is_idempotent(repo):
    seed(repo)
    log = EventLog(repo, "old")
    log.append("bug.found", "B-old", {"item": "T1", "summary": "an old bug with no task"})
    log.append("bug.found", "B-done", {"item": "", "summary": "closed long ago"})
    log.append("bug.fixed", "B-done", {"regression_test": "t::x"})
    log.append("task.added", "B-fix-x", {"parent": "P1", "title": "fix x", "tags": ["bug"]})
    log.append("bug.found", "B-linked", {"item": "B-fix-x", "summary": "has a fix task already"})
    # roborev job 1296 #5: a live `fix-<bug>` filed by hand counts as linked in BOTH runs
    log.append("bug.found", "B-hand", {"item": "", "summary": "has a hand-filed fix-B-hand"})
    log.append("task.added", "fix-B-hand", {"parent": "P1", "title": "Fix bug B-hand: by hand"})
    dry = K.bug_file_tasks(repo, dry_run=True, agent="a")
    assert dry.exit == 0 and dry.data["filed"] == ["B-old"]
    assert dry.data["linked"] == ["B-linked", "B-hand"]
    assert "fix-B-old" not in state(repo).items
    out = K.bug_file_tasks(repo, agent="a")
    assert out.exit == 0 and out.data["filed"] == ["B-old"]
    assert out.data["linked"] == ["B-linked", "B-hand"]
    assert state(repo).bugs["B-hand"].fix_task == "fix-B-hand"
    st = state(repo)
    assert st.items["fix-B-old"].parent == "P1" and st.items["fix-B-old"].globs == ["src/parser.py"]
    assert st.bugs["B-old"].fix_task == "fix-B-old"
    assert st.bugs["B-linked"].fix_task == "B-fix-x"
    assert "fix-B-done" not in st.items
    again = K.bug_file_tasks(repo, agent="a")
    assert again.exit == 2 and again.data["filed"] == [] and again.data["linked"] == []


def test_file_tasks_on_the_cli(repo):
    seed(repo)
    EventLog(repo, "old").append("bug.found", "B-old", {"item": "", "summary": "stray"})
    code, out, err = run_cli(repo, "bug", "file-tasks")
    assert code == 0, err
    assert "fix-B-old" in out
    code, _, _ = run_cli(repo, "bug", "file-tasks")
    assert code == 2


def test_the_default_ships_in_the_package_not_in_a_config_file():
    cfg = Config()
    assert cfg.bugs.file_task is True and cfg.bugs.phase == "bugs"
