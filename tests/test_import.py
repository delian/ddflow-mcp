"""Adopting ddflow on day 400 of a project, not day 1.

A queue that starts empty tells the next agent "nothing is in flight" about a repository
that may have three branches in flight and forty open items in a todo file — and the
agent believes it, because the tool said so. That is worse than having no tool.

The import has two halves and the split is the design:

* **mechanical**, here — a ticked checkbox is a fact, a `##` heading in a lessons file
  is a lesson, a branch with unmerged commits is work. Parsed, attributed to a source
  line, and *proposed*.
* **judgement**, in the `import-existing-project` prompt — which open items are really
  live, what each task writes, what depends on what. Not automatable, and a confident
  guess at it produces a wrong queue the scheduler then hands out.

These tests cover the first half and the safety rails around the second.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

OK, FAIL, NOTHING, REFUSED = 0, 1, 2, 3

TODO = """# Roadmap

## Phase 2 — billing

- [x] **P2.T1** — invoice model
- [ ] **P2.T2** — tax rules
  **Globs:** src/billing/tax.py
  **Needs:** P2.T1
- [ ] plain item with no id

## Phase 3 — reporting

- [ ] [P3.T1] monthly rollup
"""

LESSONS = """# Lessons

## Never trust a float for money
Rounding errors accumulate. Use integer minor units.

## An empty collection satisfies any check about its contents
sum([]) == 0 reads as success.
"""

ADR = """# Use Postgres as the system of record

## Status
Accepted

## Decision
Postgres, not SQLite: we need concurrent writers from three services.
"""


def _legacy(repo: Path) -> Path:
    """A repository with the history a real project has when it adopts the tool."""
    (repo / "docs" / "adr").mkdir(parents=True, exist_ok=True)
    (repo / "docs" / "todo.md").write_text(TODO)
    (repo / "docs" / "lessons.md").write_text(LESSONS)
    (repo / "docs" / "adr" / "0001-use-postgres.md").write_text(ADR)
    git = ["git", "-C", str(repo)]
    subprocess.run([*git, "add", "-A"], check=True, capture_output=True)
    subprocess.run([*git, "commit", "-qm", "history"], check=True, capture_output=True)
    subprocess.run(
        [*git, "checkout", "-q", "-b", "feature/half-done"], check=True, capture_output=True
    )
    (repo / "wip.txt").write_text("unfinished\n")
    subprocess.run([*git, "add", "-A"], check=True, capture_output=True)
    subprocess.run([*git, "commit", "-qm", "wip"], check=True, capture_output=True)
    subprocess.run([*git, "checkout", "-q", "main"], check=True, capture_output=True)
    return repo


def _plan(repo: Path, *args) -> dict:
    code, out, err = run_cli(repo, "--json", "import", *args)
    assert code in (OK, NOTHING), f"exit {code}\n{out}\n{err}"
    return json.loads(out)


# -- looking is free ------------------------------------------------------------------


def test_a_dry_run_writes_nothing(repo):
    _legacy(repo)
    run_cli(repo, "init")
    before = run_cli(repo, "--json", "board")[1]
    plan = _plan(repo)
    assert plan["found"], "it must find the project's work"
    assert plan["applied"] is False
    assert run_cli(repo, "--json", "board")[1] == before, (
        "looking at what could be imported must not import it"
    )


def test_every_proposal_says_where_it_came_from(repo):
    """An imported queue nobody can trace is one nobody can check, and the first wrong
    item teaches the operator to distrust all of it."""
    _legacy(repo)
    run_cli(repo, "init")
    for f in _plan(repo)["found"]:
        assert f["source"], f
        assert ":" in f["source"] or f["source"].endswith(".md"), f["source"]


# -- what it finds ---------------------------------------------------------------------


def test_it_finds_the_four_things_a_project_actually_has(repo):
    _legacy(repo)
    run_cli(repo, "init")
    kinds = _plan(repo)["summary"]
    assert kinds.get("phase") == 2, kinds
    assert kinds.get("lesson") == 2, kinds
    assert kinds.get("decision") == 1, kinds
    assert kinds.get("branch") == 1, "an unmerged branch IS work in flight"


def test_it_reads_the_three_ways_projects_write_an_id(repo):
    _legacy(repo)
    run_cli(repo, "init")
    ids = {f["id"] for f in _plan(repo)["found"]}
    assert "P2.T2" in ids, "**bold** ids"
    assert "P3.T1" in ids, "[bracketed] ids, which have no separator after them"
    assert any(i.endswith("plain-item-with-no-i") for i in ids), (
        "an item with no id still has to arrive, under a derived one"
    )


def test_declared_globs_and_needs_survive(repo):
    _legacy(repo)
    run_cli(repo, "init")
    t = next(f for f in _plan(repo)["found"] if f["id"] == "P2.T2")
    assert t["globs"] == ["src/billing/tax.py"]
    assert t["needs"] == ["P2.T1"]


# -- the guard rails -------------------------------------------------------------------


def test_finished_work_is_not_imported_as_a_queue(repo):
    """3,638 ticked boxes in the repository this was extracted from.

    A faithful history and useless as a queue, because none of it is work anyone will
    do. What matters for "continue from where we left off" is the OPEN items.
    """
    _legacy(repo)
    # A finished task nothing depends on — the fixture's other one, P2.T1, is pulled in
    # BECAUSE P2.T2 needs it, so on its own it proves the opposite of this test.
    todo = repo / "docs" / "todo.md"
    todo.write_text(todo.read_text() + "\n- [x] **P9.T9** — shipped long ago\n")
    run_cli(repo, "init")
    plan = _plan(repo)
    ids = {f["id"] for f in plan["found"]}
    assert "P9.T9" not in ids, "finished work nobody depends on is history, not a queue"
    assert any("NOT imported" in n for n in plan["notes"]), plan["notes"]

    with_done = _plan(repo, "--include-done")
    assert any(f["id"] == "P2.T1" and f["done"] for f in with_done["found"]), (
        "--include-done brings the history in, as completed"
    )


def test_a_finished_dependency_comes_along_so_open_work_is_not_stranded(repo):
    """The one exception to "finished work stays out", and it is load-bearing.

    `P2.T2` needs `P2.T1`, which is ticked. Leave `P2.T1` out and the open task imports
    blocked on an id the queue has never heard of — and an unknown dependency is
    treated as unmet, deliberately, so the import lands permanently stuck work while
    reporting success.
    """
    _legacy(repo)
    run_cli(repo, "init")
    plan = _plan(repo)
    assert any(f["id"] == "P2.T1" for f in plan["found"]), plan["notes"]
    assert any("depends on them" in n for n in plan["notes"]), plan["notes"]

    assert run_cli(repo, "import", "--apply")[0] == OK
    ready = {r["id"] for r in json.loads(run_cli(repo, "--json", "next")[1])["ready"]}
    assert "P2.T2" in ready, "the open task must import READY, not blocked"


def test_it_refuses_to_propose_a_whole_history(repo):
    """An import writes events into a log that is committed to git. Five thousand of
    them is not recoverable by anything short of editing history."""
    _legacy(repo)
    run_cli(repo, "init")
    big = "# Big\n\n## Everything\n\n" + "\n".join(f"- [ ] item {n}" for n in range(300))
    (repo / "docs" / "todo.md").write_text(big)
    plan = _plan(repo, "--max-tasks", "50")
    assert not [f for f in plan["found"] if f["kind"] == "task"], "over the cap: propose none"
    assert any("REFUSING" in n for n in plan["notes"]), plan["notes"]


def test_running_it_twice_changes_nothing(repo):
    """An import you cannot re-run is one you have to get right first time."""
    _legacy(repo)
    run_cli(repo, "init")
    assert run_cli(repo, "import", "--apply")[0] == OK
    after_first = run_cli(repo, "--json", "board")[1]

    code, out, _ = run_cli(repo, "import")
    assert code == NOTHING, f"a second look must find nothing new:\n{out}"
    assert run_cli(repo, "--json", "board")[1] == after_first


def test_an_empty_repository_says_so_rather_than_failing(repo):
    run_cli(repo, "init")
    code, out, _ = run_cli(repo, "import")
    assert code == NOTHING, out
    assert "Nothing to import" in out


# -- it is offered, not hidden ---------------------------------------------------------


def test_the_mcp_instructions_offer_it_when_there_is_history_and_no_queue(repo):
    from ddflow.surfaces.mcp import _instructions

    _legacy(repo)
    run_cli(repo, "init")
    text = _instructions(repo)
    assert "ddflow_import" in text, text[-600:]
    assert "queue is empty" in text.lower(), text[-600:]


def test_it_stops_offering_once_the_queue_has_work(repo):
    """Otherwise it becomes a standing banner, which is a thing readers learn to skip."""
    from ddflow.surfaces.mcp import _instructions

    _legacy(repo)
    run_cli(repo, "init")
    run_cli(repo, "import", "--apply")
    assert "This project has history" not in _instructions(repo)


def test_the_workflow_prompt_ships_and_covers_the_judgement_half(repo):
    from ddflow.services.prompts import COMMANDS, resolve_command

    assert "import-existing-project" in COMMANDS
    body = resolve_command("import-existing-project").text
    for must in ("globs", "operator", "in flight", "ddflow_import"):
        assert must in body, f"the prompt never mentions {must!r}"
    assert "guess" in body.lower(), "it must say which parts are guesses"


def test_the_command_is_reachable_over_mcp(repo):
    from ddflow.surfaces.mcp import TOOLS

    assert "ddflow_import" in TOOLS
    argv = TOOLS["ddflow_import"]["argv"]({"apply": True, "include_done": True})
    assert "--apply" in argv and "--include-done" in argv, argv
