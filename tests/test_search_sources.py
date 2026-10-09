"""`ddflow search --source`: the sources of the one search engine (B-uni-search-core.4)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.api._base import _load
from ddflow.api.schedule import schedule_define
from ddflow.core.records import Job
from ddflow.services import search as S


def _search(repo, *argv):
    code, out, err = run_cli(repo, "--json", "search", *argv)
    return code, (json.loads(out) if out.strip().startswith("{") else {}), err


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


@pytest.fixture
def full(repo):
    run_cli(repo, "init")
    run_cli(repo, "phase", "add", "P1", "--title", "Quarry phase")
    run_cli(repo, "task", "add", "T1", "--phase", "P1", "--title", "Polish the zirconium widget")
    run_cli(repo, "session", "prompt", "--text", "please fix the paprika bug")
    run_cli(repo, "session", "note", "--text", "tried cardamom, a dead end")
    code, _, err = run_cli(
        repo, "rule", "add", "--id", "r-gourd", "--title", "Peel the gourd",
        "--content", "always peel the gourd before cooking", "--new",
    )  # fmt: skip
    assert code == 0, err
    _write(repo, ".claude/skills/pickle/SKILL.md",
           "---\nname: pickle\ndescription: Brine the rhubarb\n---\nUse a tall jar.\n")  # fmt: skip
    _write(repo, ".claude/commands/ferment.md", "# Ferment the kimchi\nLeave it a week.\n")
    _write(repo, ".claude/agents/baker.md", "---\nname: baker\n---\nKnead the sourdough.\n")
    out = schedule_define(
        repo, "nightly-tamarind", {"title": "Tamarind sweep", "cadence": {"every_days": 1}}
    )
    assert out.ok, out
    return repo


@pytest.mark.parametrize(
    ("word", "source", "kind", "rid"),
    [
        ("zirconium", "records", "task", "T1"),
        ("cardamom", "sessions", "session", None),
        ("paprika", "prompts", "prompt", None),
        ("gourd", "rules", "rule", "r-gourd"),
        ("rhubarb", "skills", "skill", "pickle"),
        ("kimchi", "skills", "command", "ferment"),
        ("sourdough", "agents", "agent", "baker"),
        ("tamarind", "schedules", "schedule", "nightly-tamarind"),
    ],
)
def test_each_source_is_found_by_default_and_by_name(full, word, source, kind, rid):
    for argv in ((word,), (word, "--source", source)):
        code, body, err = _search(full, *argv)
        assert code == 0, (argv, err)
        rows = [r for r in body["rows"] if r["kind"] == kind and rid in (None, r["id"])]
        assert rows, (argv, body)


def test_source_selects_only_that_source(full):
    code, body, _ = _search(full, "gourd", "--source", "records")
    assert code == 2 and body["rows"] == []  # ran, found nothing: the rule is not a record
    _, body, _ = _search(full, "gourd cardamom", "--source", "rules,sessions")
    assert {r["kind"] for r in body["rows"]} == {"rule", "session"}
    assert body["filters"] == {"source": "rules,sessions"}


def test_source_and_kind_intersect(full):
    _, body, _ = _search(full, "rhubarb kimchi", "--source", "skills", "--kind", "command")
    assert {r["kind"] for r in body["rows"]} == {"command"}


def test_unknown_source_is_refused_with_the_choices(full):
    code, _, err = _search(full, "gourd", "--source", "nope")
    assert code == 3
    assert "unknown source nope" in err and "rules" in err and "schedules" in err


def test_a_job_is_found_by_its_command(repo):
    run_cli(repo, "init")
    log, cfg, st = _load(repo, "t")
    st.jobs["j1"] = Job(id="j1", item="T1", command="python train_zucchini.py", by="a1")
    docs = S._docs(st, log.read_all(), cfg, {"job"}, repo, {"jobs"})
    assert [(d.kind, d.id, d.state) for d in docs] == [("job", "j1", "started")]
    assert "zucchini" in docs[0].text
    st.jobs["j1"].ended_at = "2026-10-01T00:00:00Z"
    docs = S._docs(st, log.read_all(), cfg, {"job"}, repo, set())
    assert docs[0].state == "ended" and docs[0].date == "2026-10-01T00:00:00Z"


def test_file_sources_yield_only_recorded_definitions_without_a_repo(full):
    log, cfg, st = _load(full, "t")
    docs = S._docs(
        st, log.read_all(), cfg, {"rule", "skill", "command", "agent", "schedule"}, None, set()
    )
    # the rule is a recorded definition; the skill, command, agent and schedule files and the
    # merged schedule view need the repository
    assert [(d.kind, d.id) for d in docs] == [("rule", "r-gourd")]


def test_a_rule_is_found_by_its_tag_and_not_twice_by_default(repo):
    run_cli(repo, "init")
    code, _, err = run_cli(
        repo, "rule", "add", "--id", "r-tagged", "--title", "Tagged", "--content", "x",
        "--tags", "zebra", "--new",
    )  # fmt: skip
    assert code == 0, err
    code, body, _ = _search(repo, "zebra", "--source", "rules")
    assert code == 0 and [r["id"] for r in body["rows"]] == ["r-tagged"]
    _, body, _ = _search(repo, "r-tagged")
    assert [r["kind"] for r in body["rows"]] == ["rule"]  # not also its log event
    _, body, _ = _search(repo, "r-tagged", "--source", "log")
    assert body["rows"] and {r["kind"] for r in body["rows"]} == {"log"}  # the raw log is whole


def test_instruction_files_are_rules_and_a_recorded_agent_is_not_listed_twice(full):
    _write(full, ".cursor/rules/naming.mdc", "Name things plainly. Cursor turnip rule.\n")
    code, body, _ = _search(full, "turnip", "--source", "rules")
    assert code == 0 and [r["kind"] for r in body["rows"]] == ["rule"]
    from ddflow.api.defs import def_record

    assert def_record(full, "agent", "baker", {"role": "kneads sourdough"}).ok
    _, body, _ = _search(full, "sourdough", "--source", "agents")
    assert [r["id"] for r in body["rows"]] == ["baker"]


def test_a_retire_reason_is_found(full):
    from ddflow.api.defs import def_record, def_retire

    assert def_record(full, "agent", "baker", {"role": "kneads"}).ok
    assert def_retire(full, "agent", "baker", reason="obsoleted by quokka bot").ok
    _, body, _ = _search(full, "quokka", "--source", "agents", "--kind", "agent")
    assert [r["id"] for r in body["rows"]] == ["baker"]


def test_a_schedule_recorded_both_ways_is_listed_once(full):
    from ddflow.api.defs import def_record

    assert def_record(full, "schedule", "nightly-tamarind", {"title": "again"}).ok
    _, body, _ = _search(full, "tamarind", "--source", "schedules")
    assert [r["id"] for r in body["rows"]] == ["nightly-tamarind"]


def test_the_raw_log_is_whole_when_it_is_the_only_source(full):
    _, body, _ = _search(full, "zirconium", "--source", "log")
    assert {r["kind"] for r in body["rows"]} == {"log"}  # the task's creation event is here
    _, body, _ = _search(full, "zirconium")
    assert {r["kind"] for r in body["rows"]} == {"task"}  # and not repeated beside the task
