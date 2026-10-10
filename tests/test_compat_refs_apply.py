"""`ddflow upgrade` and deprecated names (B-uni-compat-refs-apply, D-compat).

What ddflow wrote into a project is rewritten by the `stale-references` migration; what the
PROJECT wrote is never edited, and the plan lists each such name as a note with the
replacement, so the person (or agent) who owns the text can change it. The vocabulary is
handed in by the surface (`services.migrations.refs.provide`).
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest
from helpers import upgrade_plan as plan

from ddflow.api._base import _load
from ddflow.infra.fsio import Managed
from ddflow.services import compat_refs as C
from ddflow.services import upgrade_apply as UA
from ddflow.services import upgrade_plan as UP
from ddflow.services.migrations import refs as R

VOCAB = C.Vocabulary(
    commands=frozenset({("claim",), ("docs",), ("docs", "show")}),
    tools=frozenset({"ddflow_next"}),
    command_aliases={("doc",): C.Renamed("docs", "0.2.0", "1.0")},
    tool_aliases={"ddflow_old_next": C.Renamed("ddflow_next", "0.2.0", "1.0")},
)


@pytest.fixture(autouse=True)
def vocabulary():
    R.provide(lambda: VOCAB)
    yield
    R.provide(None)


@pytest.fixture
def project(repo: Path) -> Path:
    from conftest import run_cli

    assert run_cli(repo, "init")[0] == 0
    managed = Managed("rules/work-queue").render("Run `ddflow doc show` first.\n")
    (repo / "AGENTS.md").write_text(
        "# Mine\n\nSee `ddflow doc show` and ddflow_old_next, and `ddflow claim`.\n\n" + managed
    )
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "refs"], check=True)
    return repo


def apply(repo: Path, categories: str | None = None) -> dict[str, Any]:
    log, cfg, st = _load(repo, "upgrader")
    return UA.apply(repo, log, cfg, st, categories=categories, agent="upgrader")


def notes(body: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        i
        for i in body["categories"]["instructions"]
        if i["id"].startswith("instructions:references")
    ]


def test_the_plan_lists_the_projects_own_deprecated_names_as_notes_with_the_replacement(project):
    (item,) = notes(plan(project))
    assert item["path"] == "AGENTS.md" and item["action"] == UP.NOTE
    details = sorted(f["detail"] for f in item["findings"])
    assert details == [
        "replace `ddflow_old_next` with `ddflow_next`",
        "replace `doc show` with `docs show`",
    ]
    text = UP.render(plan(project))
    assert "AGENTS.md names 2 deprecated ddflow name(s) in your own text" in text


def test_what_ddflow_wrote_is_a_migration_and_what_the_person_wrote_is_a_note(project):
    body = plan(project)
    (mig,) = [i for i in body["categories"]["migrations"] if i["migration"] == "stale-references"]
    assert mig["finding_count"] == 1 and mig["paths"] == ["AGENTS.md"]
    assert len(notes(body)) == 1


def test_apply_rewrites_only_ddflows_region_and_never_the_projects_text(project):
    out = apply(project)
    assert out["exit"] == 0, out["text"]
    text = (project / "AGENTS.md").read_text()
    assert "See `ddflow doc show` and ddflow_old_next, and `ddflow claim`." in text
    assert "Run `ddflow docs show` first." in text
    after = plan(project)
    assert after["categories"]["migrations"] == []
    (item,) = notes(after)
    assert item["finding_count"] == 2, "the proposals stay: nobody rewrote the person's text"
    statuses = {r["id"]: r["status"] for r in out["results"]}
    assert statuses[item["id"]] == "skipped"


def test_a_second_apply_changes_nothing(project):
    apply(project)
    snapshot = (project / "AGENTS.md").read_bytes()
    again = apply(project)
    assert again["exit"] == 0 and (project / "AGENTS.md").read_bytes() == snapshot
    assert again["backup"] == ""


def test_without_a_vocabulary_nothing_is_judged(project):
    R.provide(None)
    assert notes(plan(project)) == []


def test_names_the_process_had_no_table_for_are_one_note_not_silence(project):
    """An MCP server loads the tools only: a `ddflow <command>` in the project's text comes back
    unchecked, and the plan says so instead of reading as clean."""
    tools_only = C.Vocabulary(
        commands=frozenset(),
        tools=VOCAB.tools,
        tool_aliases=VOCAB.tool_aliases,
        check_commands=False,
    )
    R.provide(lambda: tools_only)
    items = notes(plan(project))
    unchecked = [i for i in items if i["id"].endswith(":unchecked")]
    assert len(unchecked) == 1 and "command" in unchecked[0]["summary"]
    assert "were not checked: this process has no command table loaded" in unchecked[0]["summary"]
    assert [i["path"] for i in items if not i["id"].endswith(":unchecked")] == ["AGENTS.md"]


def test_the_note_says_when_the_old_name_stops_working_from_the_alias_itself(project):
    late = C.Vocabulary(
        commands=VOCAB.commands,
        tools=VOCAB.tools,
        command_aliases={("doc",): C.Renamed("docs", "0.2.0", "2.0")},
        tool_aliases={"ddflow_old_next": C.Renamed("ddflow_next", "0.2.0", "1.0")},
    )
    R.provide(lambda: late)
    (item,) = [i for i in notes(plan(project)) if i["path"] == "AGENTS.md"]
    assert item["fix"].endswith("until 1.0 / 2.0")
