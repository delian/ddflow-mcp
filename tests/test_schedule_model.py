"""Scheduled-job definitions (B-sched-model, decision D-sched-no-daemon).

A job is defined in the log (`schedule.*` events), in `.ddflow/schedules/<id>.toml`, or by
the `[cadence]` config; every surface shows the merge, validated. These tests pin the
fold, each source, the precedence between them, the validation that refuses a broken
definition before it is written, and the conflict rules two jobs are serialised by.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.api import schedule as A
from ddflow.config import Config
from ddflow.core.events import PROVENANCE_KINDS, Event
from ddflow.core.model import HANDLERS, Schedule, fold
from ddflow.services import schedule as SV
from ddflow.surfaces import cli
from ddflow.surfaces.commands.schedule import add_schedule_parser
from ddflow.surfaces.context import Ctx

OK, FAIL, NOTHING, REFUSED = 0, 1, 2, 3
KINDS = ("schedule.defined", "schedule.updated", "schedule.removed")


def _ev(kind: str, subject: str, data: dict, lamport: int, agent: str = "a") -> Event:
    return Event(
        kind=kind, subject=subject, data=data, lamport=lamport, agent=agent, ts=f"t{lamport}"
    )


@pytest.fixture
def proj(repo: Path) -> Path:
    code, out, err = run_cli(repo, "init")
    assert code == 0, (code, out, err)
    return repo


def _file(repo: Path, name: str, text: str) -> None:
    d = repo / ".ddflow" / "schedules"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(text)


# -- the fold ------------------------------------------------------------------------------


def test_the_three_kinds_fold_strictly_and_survive_compaction():
    assert set(KINDS) <= set(HANDLERS)
    assert set(KINDS) <= PROVENANCE_KINDS
    fold([_ev(k, "j", {"reason": "r"}, i) for i, k in enumerate(KINDS, 1)], strict=True)


def test_a_definition_replaces_an_update_merges_and_a_removal_is_kept():
    st = fold(
        [
            _ev(
                "schedule.defined",
                "j",
                {"title": "one", "cadence": {"every_days": 7}, "tags": ["x"]},
                1,
                "alice",
            ),
            _ev("schedule.updated", "j", {"mode": "fix"}, 2, "bob"),
        ]
    )
    j = st.schedules["j"]
    assert (j.title, j.cadence, j.mode, j.tags, j.by, j.at) == (
        "one",
        {"every_days": 7},
        "fix",
        ["x"],
        "alice",
        "t1",
    )
    # a second definition is the WHOLE definition: what it omits returns to the default
    st = fold(
        [
            _ev("schedule.defined", "j", {"title": "one", "tags": ["x"]}, 1, "alice"),
            _ev("schedule.defined", "j", {"title": "two"}, 2, "bob"),
            _ev("schedule.removed", "j", {"reason": "obsolete"}, 3),
        ]
    )
    j = st.schedules["j"]
    assert (j.title, j.tags, j.removed, j.by, j.at) == ("two", [], "obsolete", "alice", "t1")
    st = fold(
        [
            _ev("schedule.defined", "j", {"title": "one"}, 1),
            _ev("schedule.removed", "j", {"reason": "obsolete"}, 2),
            _ev("schedule.defined", "j", {"title": "back"}, 3),
        ]
    )
    assert (st.schedules["j"].title, st.schedules["j"].removed) == ("back", "")


def test_an_update_or_removal_before_its_definition_is_not_dropped():
    st = fold([_ev("schedule.updated", "j", {"mode": "fix"}, 1)])
    assert st.schedules["j"].mode == "fix"
    st = fold([_ev("schedule.removed", "k", {}, 1)])
    assert st.schedules["k"].removed == "removed"


def test_the_fold_does_not_share_a_list_with_the_event():
    ev = _ev("schedule.defined", "j", {"needs": ["a"], "budget": {"max_items": 1}}, 1)
    st = fold([ev])
    st.schedules["j"].needs.append("b")
    st.schedules["j"].budget["max_bugs"] = 2
    assert ev.data == {"needs": ["a"], "budget": {"max_items": 1}}


# -- one definition ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("spec", "says"),
    [
        ({}, "cadence is required"),
        ({"cadence": {"every_days": 0}}, "every_days must be a number of days above 0"),
        ({"cadence": {"every_days": float("nan")}}, "every_days"),
        ({"cadence": {"every_tasks": 1.5}}, "every_tasks must be a whole number"),
        ({"cadence": {"every_tasks": True}}, "every_tasks must be a whole number"),
        ({"cadence": {"every_days": 1, "every_tasks": 1}}, "exactly one of"),
        ({"cadence": {"hourly": 1}}, "exactly one of"),
        ({"cadence": {"every_days": 1}, "mode": "auto"}, "mode must be one of report, fix"),
        ({"cadence": {"every_days": 1}, "missed": "all"}, "missed must be one of skip, once"),
        (
            {"cadence": {"every_days": 1}, "budget": {"max_cost": 1}},
            "budget.max_cost is not a budget",
        ),
        ({"cadence": {"every_days": 1}, "budget": {"max_items": -1}}, "budget.max_items must be"),
        ({"cadence": {"every_days": 1}, "jitter": -5}, "jitter must be whole minutes"),
        ({"cadence": {"every_days": 1}, "enabled": "yes"}, "enabled must be true or false"),
        ({"cadence": {"every_days": 1}, "scope_glob": ["a"]}, "unknown field(s) scope_glob"),
        ({"cadence": {"every_days": 1}, "needs": ["a.b"]}, "a plain TOML key"),
        ({"cadence": {"every_days": 1}, "concurrency_group": "a b"}, "concurrency_group"),
        ({"cadence": {"every_days": 1}, "needs": ["j"]}, "j cannot need itself"),
    ],
)
def test_a_bad_definition_is_refused_and_says_why(spec, says):
    job, errors = SV.build("j", spec)
    assert job is None
    assert any(says in e for e in errors), errors


def test_a_job_id_is_a_plain_key():
    for bad in ("a.b", "a b", '"q"', "é", ""):
        assert SV.build(bad, {"cadence": {"every_days": 1}})[0] is None
    job, errors = SV.build("Bug-audit_2", {"cadence": {"every_days": 1}})
    assert job is not None and not errors


def test_a_good_definition_is_typed_and_defaulted():
    job, errors = SV.build(
        "audit",
        {
            "title": " Weekly audit ",
            "cadence": {"every_days": 7},
            "needs": "tests, lint",
            "scope_globs": ["ddflow/**"],
            "budget": {"max_items": 3},
        },
    )
    assert not errors and job is not None
    assert (job.title, job.needs, job.mode, job.missed, job.enabled, job.escalate) == (
        "Weekly audit",
        ["tests", "lint"],
        "report",
        "skip",
        True,
        True,
    )


# -- the sources and their precedence ------------------------------------------------------


def test_the_cadence_config_is_shown_as_jobs_without_changing_it():
    cfg = Config()
    jobs, errors = SV.from_cadence(cfg)
    assert not errors
    by = {j.id: j for j in jobs}
    assert set(by) == {
        "integration_tests",
        "dedupe_sweep",
        "architecture_review",
        "mutation_tests",
        "lessons_pass",
    }
    assert by["integration_tests"].cadence == {
        "every_tasks": cfg.cadence.integration_tests_every_tasks
    }
    assert by["architecture_review"].cadence == {
        "every_phases": cfg.cadence.architecture_review_every_phases
    }
    assert all(j.enabled for j in jobs)


def test_a_cadence_pass_set_to_zero_is_shown_disabled():
    cfg = Config()
    cfg.cadence.mutation_tests_every_phases = 0
    by = {j.id: j for j in SV.from_cadence(cfg)[0]}
    assert by["mutation_tests"].enabled is False


def test_every_days_replaces_its_count_pass_unless_any_entry_is_malformed():
    """The same rule `count_due` / `phase_overdue` apply, so the view cannot disagree with
    what `ddflow cadence` reports as due."""
    cfg = Config()
    cfg.cadence.every_days = ["dedupe_sweep=7", "bug_hunt=3.5"]
    by = {j.id: j for j in SV.from_cadence(cfg)[0]}
    assert by["dedupe_sweep"].cadence == {"every_days": 7.0}
    assert by["bug_hunt"].cadence == {"every_days": 3.5}
    cfg.cadence.every_days = ["dedupe_sweep=7", "broken"]
    jobs, errors = SV.from_cadence(cfg)
    by = {j.id: j for j in jobs}
    assert by["dedupe_sweep"].cadence == {"every_tasks": cfg.cadence.dedupe_sweep_every_tasks}
    assert len(jobs) == len(SV.COUNT_PASSES)
    assert errors and "'broken'" in errors[0]
    # a name `ddflow cadence` accepts is shown as it is, never refused here
    cfg.cadence.every_days = ["bug hunt=7"]
    assert [j.id for j in SV.from_cadence(cfg)[0]][-1] == "bug hunt"


def test_a_job_file_loads_and_a_broken_one_is_reported_not_fatal(proj):
    _file(proj, "audit.toml", 'title = "Audit"\ncadence = { every_days = 7 }\nmode = "fix"\n')
    _file(proj, "bad.toml", "cadence = [\n")
    _file(proj, "typo.toml", "cadence = { every_days = 7 }\nscope_glob = ['a']\n")
    _file(proj, "named.toml", 'id = "other"\ncadence = { every_days = 7 }\n')
    out = A.schedule_list(proj)
    rows = {r["id"]: r for r in out.data["rows"]}
    assert rows["audit"]["source"] == "file:.ddflow/schedules/audit.toml"
    assert rows["audit"]["mode"] == "fix"
    assert not {"bad", "typo", "named", "other"} & set(rows)
    errors = " | ".join(out.data["errors"])
    assert ".ddflow/schedules/bad.toml: cannot read it" in errors
    assert ".ddflow/schedules/typo.toml: unknown field(s) scope_glob" in errors
    assert "named.toml: id 'other' differs from the file name" in errors


def test_the_log_shadows_a_file_which_shadows_the_cadence_config(proj):
    _file(proj, "dedupe_sweep.toml", "cadence = { every_days = 2 }\n")
    row = A.schedule_show(proj, "dedupe_sweep").data
    assert (row["source"], row["shadows"], row["cadence"]) == (
        "file:.ddflow/schedules/dedupe_sweep.toml",
        ["cadence"],
        {"every_days": 2},
    )
    out = A.schedule_define(proj, "dedupe_sweep", {"cadence": {"every_days": 1}})
    assert out.exit == OK, out
    assert out.data["shadows"] == ["file:.ddflow/schedules/dedupe_sweep.toml"]
    row = A.schedule_show(proj, "dedupe_sweep").data
    assert (row["source"], row["cadence"]) == ("log", {"every_days": 1})
    assert row["shadows"] == ["file:.ddflow/schedules/dedupe_sweep.toml", "cadence"]


def test_removing_a_recorded_job_hides_the_same_id_below_it(proj):
    assert A.schedule_define(proj, "lessons_pass", {"cadence": {"every_days": 9}}).exit == OK
    out = A.schedule_remove(proj, "lessons_pass", reason="we do this by hand")
    assert out.exit == OK, out
    assert "lessons_pass" not in {r["id"] for r in A.schedule_list(proj).data["rows"]}
    shown = A.schedule_show(proj, "lessons_pass")
    assert shown.exit == FAIL and "removed: we do this by hand" in shown.reason


# -- validation against the other jobs -----------------------------------------------------


def test_a_needs_naming_no_job_or_closing_a_cycle_is_refused_before_it_is_written(proj):
    before = (proj / ".ddflow" / "events").stat().st_mtime_ns
    out = A.schedule_define(proj, "a", {"cadence": {"every_days": 1}, "needs": ["ghost"]})
    assert out.exit == FAIL and "a needs ghost, which is not a job" in out.reason
    assert (
        A.schedule_define(proj, "a", {"cadence": {"every_days": 1}, "needs": ["dedupe_sweep"]}).exit
        == OK
    )
    assert A.schedule_define(proj, "b", {"cadence": {"every_days": 1}, "needs": ["a"]}).exit == OK
    out = A.schedule_update(proj, "a", {"needs": ["b"]})
    assert out.exit == FAIL and "needs cycle: a -> b -> a" in out.reason
    assert A.schedule_show(proj, "a").data["needs"] == ["dedupe_sweep"]
    assert A.schedule_show(proj, "a").data["needed_by"] == ["b"]
    del before


def test_a_cycle_through_files_is_reported_by_list(proj):
    _file(proj, "x.toml", "cadence = { every_days = 1 }\nneeds = ['y']\n")
    _file(proj, "y.toml", "cadence = { every_days = 1 }\nneeds = ['x']\n")
    assert "needs cycle: x -> y -> x" in A.schedule_list(proj).data["errors"]


def test_removing_a_job_another_needs_is_refused(proj):
    assert A.schedule_define(proj, "a", {"cadence": {"every_days": 1}}).exit == OK
    assert A.schedule_define(proj, "b", {"cadence": {"every_days": 1}, "needs": ["a"]}).exit == OK
    out = A.schedule_remove(proj, "a", reason="x")
    assert out.exit == REFUSED and out.data["needed_by"] == ["b"]
    assert A.schedule_remove(proj, "a", reason=" ").exit == FAIL


def test_only_a_recorded_job_is_updated_here(proj):
    _file(proj, "f.toml", "cadence = { every_days = 1 }\n")
    out = A.schedule_update(proj, "f", {"mode": "fix"})
    assert (
        out.exit == FAIL
        and "defined by file:.ddflow/schedules/f.toml; change it there" in out.reason
    )
    assert A.schedule_update(proj, "nope", {"mode": "fix"}).exit == FAIL
    assert A.schedule_define(proj, "r", {"cadence": {"every_days": 1}}).exit == OK
    assert A.schedule_update(proj, "r", {"mode": "report"}).exit == NOTHING
    bad = A.schedule_update(proj, "r", {"mode": "loud"})
    assert bad.exit == FAIL and "mode must be one of" in bad.reason
    assert A.schedule_update(proj, "r", {"needs": ["r"]}).exit == FAIL


# -- conflicts -----------------------------------------------------------------------------


def test_two_jobs_conflict_by_group_or_overlapping_scope_and_shared_globs_are_exempt():
    a = Schedule(id="a", scope_globs=["ddflow/**"], concurrency_group="heavy")
    b = Schedule(id="b", scope_globs=["ddflow/api/*.py"])
    c = Schedule(id="c", scope_globs=["docs/**"], concurrency_group="heavy")
    d = Schedule(id="d", scope_globs=["CHANGELOG.md"])
    e = Schedule(id="e")
    assert SV.conflicts(a, b) == ["scope ddflow/** overlaps ddflow/api/*.py"]
    assert SV.conflicts(a, c) == ["concurrency group heavy"]
    assert SV.conflicts(d, Schedule(id="x", scope_globs=["CHANGELOG.md"]), ["CHANGELOG.md"]) == []
    assert SV.conflicts(b, c) == []
    assert SV.conflicts(a, e) == []


def test_show_lists_only_enabled_jobs_it_may_not_run_beside(proj):
    for jid, spec in {
        "a": {"scope_globs": ["src/**"]},
        "b": {"scope_globs": ["src/x.py"]},
        "c": {"scope_globs": ["src/y.py"], "enabled": False},
        "d": {"concurrency_group": "g"},
        "e": {"concurrency_group": "g"},
    }.items():
        assert A.schedule_define(proj, jid, {"cadence": {"every_days": 1}, **spec}).exit == OK
    assert A.schedule_show(proj, "a").data["conflicts"] == [
        {"id": "b", "why": ["scope src/** overlaps src/x.py"]}
    ]
    assert A.schedule_show(proj, "d").data["conflicts"] == [
        {"id": "e", "why": ["concurrency group g"]}
    ]


# -- reading -------------------------------------------------------------------------------


def test_runs_are_the_cadence_records_under_the_jobs_id(proj):
    code, out, err = run_cli(proj, "cadence", "--ran", "dedupe_sweep")
    assert code == 0, (code, out, err)
    runs = A.schedule_show(proj, "dedupe_sweep").data["runs"]
    assert len(runs) == 1


def test_list_filters_and_search_needs_every_word(proj):
    assert (
        A.schedule_define(
            proj,
            "bug-audit",
            {"title": "Weekly bug audit", "cadence": {"every_days": 7}, "tags": ["audit"]},
        ).exit
        == OK
    )
    assert (
        A.schedule_define(
            proj, "deps", {"cadence": {"every_days": 7}, "enabled": False, "tags": ["audit"]}
        ).exit
        == OK
    )
    assert [r["id"] for r in A.schedule_list(proj, tag="audit").data["rows"]] == [
        "bug-audit",
        "deps",
    ]
    assert [
        r["id"] for r in A.schedule_list(proj, tag="audit", enabled_only=True).data["rows"]
    ] == ["bug-audit"]
    assert A.schedule_list(proj, tag="none").exit == NOTHING
    assert [r["id"] for r in A.schedule_search(proj, "WEEKLY audit").data["rows"]] == ["bug-audit"]
    assert A.schedule_search(proj, "weekly deps").exit == NOTHING
    assert A.schedule_search(proj, "  ").exit == NOTHING


# -- the CLI verbs -------------------------------------------------------------------------


def _cli(repo: Path, *argv: str, capsys) -> tuple[int, str, str]:
    """The `schedule` verbs through the real global parser, mounted the one way the CLI
    module will mount them."""
    p = cli.build_parser()
    sub = next(a for a in p._actions if isinstance(a, argparse._SubParsersAction))
    add_schedule_parser(sub)
    args = p.parse_args(["--repo", str(repo), *argv])
    code = int(args.fn(args, Ctx(args)))
    cap = capsys.readouterr()
    return code, cap.out, cap.err


def test_the_cli_verbs_print_and_json_carries_the_rows(proj, capsys):
    assert (
        A.schedule_define(proj, "audit", {"title": "Audit", "cadence": {"every_days": 7}}).exit
        == OK
    )
    code, out, _ = _cli(proj, "schedule", "list", capsys=capsys)
    assert code == OK and "audit" in out and "days=7" in out and "[log]" in out
    code, out, _ = _cli(proj, "--json", "schedule", "list", capsys=capsys)
    assert code == OK and "audit" in {r["id"] for r in json.loads(out)}
    code, out, _ = _cli(proj, "schedule", "show", "audit", capsys=capsys)
    assert code == OK and "source    log" in out and "runs      0" in out
    code, _, err = _cli(proj, "schedule", "show", "nope", capsys=capsys)
    assert code == FAIL and "no such job 'nope'" in err
    code, out, _ = _cli(proj, "schedule", "search", "zzz", capsys=capsys)
    assert code == NOTHING and "no job matches" in out
