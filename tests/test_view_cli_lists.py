"""`task|phase|bug|research list`: the CLI over the shared list engine."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli


@pytest.fixture
def proj(repo):
    assert run_cli(repo, "init")[0] == 0
    run_cli(repo, "phase", "add", "P1", "--title", "Auth")
    run_cli(repo, "phase", "add", "P2", "--title", "Billing")
    run_cli(repo, "task", "add", "P1.T1", "--phase", "P1", "--title", "login", "--tags", "web")
    run_cli(repo, "task", "add", "P1.T2", "--phase", "P1", "--title", "logout")
    run_cli(repo, "task", "add", "P2.T1", "--phase", "P2", "--title", "invoice")
    # --no-task keeps the task counts below about the three tasks filed by hand.
    bug = ("bug", "found", "--no-task")
    run_cli(repo, *bug, "--id", "B1", "--summary", "crash on login", "--item", "P1.T1")
    run_cli(repo, *bug, "--id", "B2", "--summary", "bad total", "--item", "P2.T1")
    run_cli(repo, "bug", "invalid", "B2", "--reason", "not a bug")
    for rid, q, v in (("R1", "does x work", "CONFIRMED"), ("R2", "does y work", "REFUTED")):
        argv = ("research", "--id", rid, "--question", q, "--verdict", v, "--probe", "ran it")
        assert run_cli(repo, *argv)[0] == 0
    return repo


def _ids(out: str) -> list[str]:
    return [r["id"] for r in json.loads(out)["rows"]]


def test_task_list_lists_every_task_and_filters(proj):
    code, out, _ = run_cli(proj, "task", "list")
    assert code == 0
    for t in ("P1.T1", "P1.T2", "P2.T1"):
        assert t in out
    code, out, _ = run_cli(proj, "--json", "task", "list", "--phase", "P2")
    assert code == 0 and _ids(out) == ["P2.T1"]
    code, out, _ = run_cli(proj, "--json", "task", "list", "--tag", "web", "--limit", "1")
    assert _ids(out) == ["P1.T1"]


def test_phase_list_shows_progress(proj):
    code, out, _ = run_cli(proj, "phase", "list")
    assert code == 0
    p1 = next(line for line in out.splitlines() if "P1" in line)
    assert "0/2" in p1
    code, out, _ = run_cli(proj, "--json", "phase", "list")
    rows = {r["id"]: r for r in json.loads(out)["rows"]}
    assert rows["P1"]["done"] == 0 and rows["P1"]["total"] == 2
    assert rows["P2"]["total"] == 1


def test_bug_list_defaults_to_open_and_all_widens(proj):
    code, out, _ = run_cli(proj, "--json", "bug", "list")
    assert code == 0 and _ids(out) == ["B1"]
    code, out, _ = run_cli(proj, "--json", "bug", "list", "--all")
    assert sorted(_ids(out)) == ["B1", "B2"]
    code, out, _ = run_cli(proj, "--json", "bug", "list", "--state", "invalid")
    assert _ids(out) == ["B2"]


def test_bug_list_open_and_all_default_nothing_is_exit_2(repo):
    run_cli(repo, "init")
    code, out, _ = run_cli(repo, "bug", "list")
    assert code == 2 and "No bug records" in out


def test_research_list_shows_verdicts(proj):
    code, out, err = run_cli(proj, "research", "list")
    assert code == 0, (out, err)
    assert "confirmed" in out and "refuted" in out
    code, out, _ = run_cli(proj, "--json", "research", "list", "--state", "refuted")
    assert _ids(out) == ["R2"]


def test_research_add_still_requires_its_fields(proj):
    code, _, err = run_cli(proj, "research", "add", "--verdict", "CONFIRMED")
    assert code != 0 and "--question" in err
    code, _, err = run_cli(proj, "research", "--question", "q")
    assert code != 0 and "--verdict" in err


def test_a_filter_the_kind_cannot_honour_is_not_offered(proj):
    code, _, err = run_cli(proj, "bug", "list", "--tag", "x")
    assert code != 0 and "--tag" in err


@pytest.mark.parametrize("kind", ["task", "phase", "bug", "research"])
def test_help_names_the_filters(proj, kind):
    code, out, _ = run_cli(proj, kind, "list", "--help")
    assert code == 0 and "--limit" in out and "--since" in out


def test_research_add_verb_form_records_and_an_empty_value_counts_as_given(proj):
    argv = ("research", "add", "--id", "R3", "--question", "q3", "--verdict", "THEORETICAL")
    assert run_cli(proj, *argv)[0] == 0
    assert "R3" in run_cli(proj, "research", "list")[1]
    # `--question ""` was GIVEN: whatever refuses it, it is not "required arguments".
    _, _, err = run_cli(proj, "research", "--question", "", "--verdict", "THEORETICAL")
    assert "required" not in err


def test_the_global_agent_is_identity_not_a_filter(proj):
    """B-view-cli-lists review: `--agent X` is mirrored onto every subparser, so a list
    filter spelled --agent would make `ddflow --agent me bug list` refuse (and
    `--agent me task list` silently show only my leases)."""
    assert run_cli(proj, "bug", "list", agent="alice")[0] == 0
    assert run_cli(proj, "research", "list", agent="alice")[0] == 0
    code, out, _ = run_cli(proj, "--json", "task", "list", agent="alice")
    assert code == 0 and len(_ids(out)) == 3
    code, out, _ = run_cli(proj, "--json", "task", "list", "--owner", "alice")
    assert code == 2 and json.loads(out)["filters"] == {"owner": "alice"}


def test_list_flags_on_the_add_form_are_refused_not_dropped(proj):
    argv = (
        "research",
        "--id",
        "R9",
        "--question",
        "q9",
        "--verdict",
        "THEORETICAL",
        "--limit",
        "3",
    )
    code, _, err = run_cli(proj, *argv)
    assert code != 0 and "--limit" in err and "research list" in err
    assert "R9" not in run_cli(proj, "research", "list")[1]


def test_json_carries_the_reason_when_nothing_matches(proj):
    code, out, _ = run_cli(proj, "--json", "task", "list", "--tag", "nope")
    assert code == 2 and "No task records" in json.loads(out)["reason"]


def test_limit_zero_is_refused_not_defaulted(proj):
    code, _, err = run_cli(proj, "task", "list", "--limit", "0")
    assert code == 3 and "limit must be at least 1" in err
