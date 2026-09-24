"""Architectural decisions, and the recall surface that makes them consulted.

The failure these exist to prevent is not forgetting a decision — it is *re-deciding*
it: the operator saying the same thing a third time, the agent re-proposing what was
rejected, two modules ending up on opposite sides of a question that was settled months
ago. Nothing in the code records why it is the way it is, so unless the reasoning is
written down and reachable, it is gone.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import finish, run_cli

OK, FAIL, NOTHING, REFUSED = 0, 1, 2, 3


@pytest.fixture
def proj(repo):
    run_cli(repo, "init")
    run_cli(repo, "phase", "add", "P1", "--title", "Core")
    run_cli(
        repo,
        "task",
        "add",
        "P1.T1",
        "--phase",
        "P1",
        "--title",
        "storage",
        "--globs",
        "src/storage/db.py",
    )
    run_cli(
        repo, "task", "add", "P1.T2", "--phase", "P1", "--title", "ui", "--globs", "src/ui/page.py"
    )
    return repo


def _add(repo, **kw):
    args = ["decision", "add"]
    for k, v in kw.items():
        args += [f"--{k.replace('_', '-')}", v]
    return run_cli(repo, *args)


# -- recording ------------------------------------------------------------------------


def test_a_decision_must_say_what_was_DECIDED(proj):
    code, _, err = run_cli(proj, "decision", "add", "--title", "we talked about storage")
    assert code != OK and "decision" in err.lower()


def test_a_decision_without_globs_warns_that_it_cannot_be_surfaced(proj):
    """A decision nobody can be handed is a decision that will be re-taken."""
    code, out, _ = _add(
        proj, title="Use UTC everywhere", decision="All timestamps are UTC at rest and in transit."
    )
    assert code == OK
    assert "cannot be surfaced automatically" in out


def test_a_decision_reaches_the_item_whose_files_it_governs(proj):
    """The mechanism that makes a decision consulted rather than merely filed."""
    _add(
        proj,
        title="Storage is SQLite with WAL",
        decision="One file, WAL mode, BEGIN IMMEDIATE for writes.",
        globs="src/storage/*",
        by="operator",
    )
    _add(
        proj,
        title="UI strings live in one catalogue",
        decision="No literal user-facing strings outside src/ui/strings.py.",
        globs="src/ui/*",
    )

    out = json.loads(run_cli(proj, "--json", "decision", "applicable", "P1.T1")[1])
    titles = [d["title"] for d in out["applicable"]]
    assert titles == ["Storage is SQLite with WAL"], titles
    assert "UI strings" not in json.dumps(out), "a UI decision reached a storage task"


def test_project_wide_decisions_apply_to_everything(proj):
    _add(proj, title="Use UTC everywhere", decision="All timestamps are UTC.")
    out = json.loads(run_cli(proj, "--json", "decision", "applicable", "P1.T2")[1])
    assert [d["title"] for d in out["project_wide"]] == ["Use UTC everywhere"]


def test_the_brief_carries_the_governing_decisions(proj):
    _add(
        proj,
        title="Storage is SQLite with WAL",
        decision="One file, WAL mode.",
        globs="src/storage/*",
    )
    brief = run_cli(proj, "brief", "--item", "P1.T1")[1]
    assert "Architectural decisions governing these files" in brief
    assert "Storage is SQLite with WAL" in brief
    assert "Binding unless the operator says otherwise" in brief


# -- supersession: history is kept, never rewritten -------------------------------------


def test_superseding_keeps_the_old_decision_and_points_at_the_new_one(proj):
    _add(
        proj,
        id="D1",
        title="Storage is JSON files",
        decision="One JSON file per record.",
        globs="src/storage/*",
    )
    _add(
        proj,
        id="D2",
        title="Storage is SQLite",
        decision="One SQLite file, WAL mode.",
        globs="src/storage/*",
        supersedes="D1",
    )

    live = json.loads(run_cli(proj, "--json", "decision", "list")[1])
    assert [d["id"] for d in live] == ["D2"], "a superseded decision is still live"

    allof = json.loads(run_cli(proj, "--json", "decision", "list", "--all")[1])
    old = next(d for d in allof if d["id"] == "D1")
    assert old["status"] == "superseded" and old["superseded_by"] == "D2", old
    assert old["decision"], "the superseded decision's content must survive"


def test_a_superseded_decision_is_not_surfaced_as_applicable(proj):
    """Following a reversed decision is worse than not finding one at all."""
    _add(proj, id="D1", title="JSON files", decision="one file per record", globs="src/storage/*")
    _add(proj, id="D2", title="SQLite", decision="one db", globs="src/storage/*", supersedes="D1")
    out = json.loads(run_cli(proj, "--json", "decision", "applicable", "P1.T1")[1])
    assert [d["id"] for d in out["applicable"]] == ["D2"]


def test_supersede_requires_a_replacement(proj):
    _add(proj, id="D1", title="x", decision="y")
    code, _out, _err = run_cli(proj, "decision", "supersede", "D1", "--by", "")
    assert code != OK


def test_decisions_survive_into_the_reconstruction(proj):
    """The least recoverable thing in a project: the code shows WHAT was built and
    never why, nor what was rejected on the way."""
    _add(
        proj,
        title="Storage is SQLite with WAL",
        decision="One file, WAL mode, BEGIN IMMEDIATE.",
        alternatives="Postgres — rejected: no server allowed in this deployment.",
        globs="src/storage/*",
    )
    doc = run_cli(proj, "replay")[1]
    assert "Storage is SQLite with WAL" in doc
    assert "BEGIN IMMEDIATE" in doc
    assert "no server allowed" in doc, "the rejected alternative must survive"


# -- recall ------------------------------------------------------------------------------


def test_recall_searches_every_corpus_at_once(proj):
    sid = run_cli(proj, "--json", "session", "start")[1]
    sid = json.loads(sid)["session"]
    run_cli(
        proj,
        "session",
        "prompt",
        sid,
        "--text",
        "Please make sure durations are always stored in milliseconds",
    )
    _add(
        proj,
        title="Durations are milliseconds",
        decision="Integer milliseconds everywhere.",
        globs="src/*",
    )
    run_cli(
        proj,
        "lesson",
        "add",
        "--title",
        "Float seconds drift when summed",
        "--rule",
        "Aggregating float seconds over a long window loses precision.",
    )
    run_cli(
        proj,
        "research",
        "--question",
        "float or int for durations?",
        "--verdict",
        "CONFIRMED",
        "--probe",
        "bench.py",
        "--probe-output",
        "float drifted 0.3s over 1e6 adds",
        "--claim",
        "integers are exact",
    )
    run_cli(
        proj, "bug", "found", "--id", "B1", "--summary", "duration rounding lost 2 seconds per day"
    )

    out = json.loads(run_cli(proj, "--json", "recall", "durations milliseconds")[1])
    assert "decisions" in out, out.keys()
    assert "lessons" in out or "research" in out, out.keys()
    kinds = set(out)
    assert len(kinds) >= 3, f"recall found only {kinds}"


def test_recall_finds_a_prior_prompt_so_the_operator_need_not_repeat_it(proj):
    sid = json.loads(run_cli(proj, "--json", "session", "start")[1])["session"]
    run_cli(
        proj,
        "session",
        "prompt",
        sid,
        "--text",
        "Never use floating point for money; use integer minor units",
    )
    out = json.loads(run_cli(proj, "--json", "recall", "floating point money")[1])
    assert "prompts" in out, out.keys()
    assert "integer minor units" in json.dumps(out)


def test_recall_returns_exit_two_when_it_finds_nothing(proj):
    assert run_cli(proj, "recall", "quantum teleportation subsystem")[0] == NOTHING


def test_recall_labels_a_decision_as_binding(proj):
    _add(proj, title="Durations are milliseconds", decision="Integer ms.", globs="src/*")
    out = run_cli(proj, "recall", "durations")[1]
    assert "DECISION" in out and "binding" in out
    assert "prompt to CHECK, not a verdict" in out


def test_recall_respects_its_character_budget(proj):
    for i in range(30):
        run_cli(
            proj,
            "lesson",
            "add",
            "--title",
            f"padding lesson {i} about storage",
            "--rule",
            "x " * 300,
        )
    out = run_cli(proj, "recall", "storage", "--max-chars", "800")[1]
    assert len(out) < 2000, f"budget ignored: {len(out)} chars"


# -- status --------------------------------------------------------------------------------


def test_status_answers_what_has_been_completed(proj):
    run_cli(proj, "claim", "P1.T1", "--no-worktree")
    assert finish(proj, "P1.T1", "--sha", "abc1234")[0] == OK

    data = json.loads(run_cli(proj, "--json", "status")[1])
    assert data["tasks"]["done"] == 1
    assert [t["id"] for t in data["completed_tasks"]] == ["P1.T1"]
    assert data["ready_now"], "P1.T2 should be ready"

    human = run_cli(proj, "status")[1]
    assert "Completed:" in human and "P1.T1" in human
    assert "Ready to start:" in human and "P1.T2" in human


def test_status_surfaces_loops_and_recoverable_work(proj):
    run_cli(proj, "task", "add", "A", "--phase", "P1", "--needs", "B")
    run_cli(proj, "task", "add", "B", "--phase", "P1", "--needs", "A")
    data = json.loads(run_cli(proj, "--json", "status")[1])
    assert any(f["kind"] == "dependency_cycle" for f in data["loops"])
    assert "⚠" in run_cli(proj, "status")[1]
