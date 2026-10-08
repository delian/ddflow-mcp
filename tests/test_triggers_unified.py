"""One due engine (B-uni-triggers): the count, calendar and registry slices.

The oracle functions below are the PRE-unification implementations, kept verbatim so the
single engine is checked against what the duplicated sites used to compute over a whole grid
of inputs (L-Bf72f9fb741: pin every definition in one table before unifying them).
"""

from __future__ import annotations

import itertools
import math
import time

from ddflow.config import Config
from ddflow.core import clock
from ddflow.core.model import Item, State
from ddflow.services import cadence as CA
from ddflow.services import rates as RT
from ddflow.services import schedule as SV


def _state(tasks: int, phases: int, runs: dict[str, str]) -> State:
    st = State()
    for n in range(tasks):
        st.items[f"T{n}"] = Item(id=f"T{n}", kind="task", title="t", state="done")
    for n in range(phases):
        st.items[f"P{n}"] = Item(id=f"P{n}", kind="phase", title="p", state="done")
    st.items["open"] = Item(id="open", kind="task", title="t", state="open")
    for name, result in runs.items():
        st.cadences[name] = [{"at": "2026-01-01T00:00:00Z", "result": result}]
    return st


def _oracle_count_due(st, cfg, replaced=frozenset()):
    done_tasks = sum(1 for i in st.items.values() if i.kind == "task" and i.state == "done")
    done_phases = sum(1 for i in st.items.values() if i.kind == "phase" and i.state == "done")
    c = cfg.cadence
    due = []
    for name, every, unit, count in (
        ("integration_tests", c.integration_tests_every_tasks, "tasks", done_tasks),
        ("dedupe_sweep", c.dedupe_sweep_every_tasks, "tasks", done_tasks),
        ("architecture_review", c.architecture_review_every_phases, "phases", done_phases),
        ("mutation_tests", c.mutation_tests_every_phases, "phases", done_phases),
        ("lessons_pass", c.lessons_pass_every_phases, "phases", done_phases),
    ):
        if name in replaced:
            continue
        runs = st.cadences.get(name, [])
        at_last = int(runs[-1].get("result", "0") or 0) if runs else 0
        since = count - at_last
        if every > 0 and since >= every:
            due.append({"cadence": name, "since": since, "every": every, "unit": unit})
    return due


GRID = list(
    itertools.product(
        (0, 3, 4, 5, 9, 10),  # done tasks
        (0, 1, 2, 3, 4, 8),  # done phases
        ("", "0", "3", "5"),  # the last recorded result of every pass ("" = never ran)
        (frozenset(), frozenset({"dedupe_sweep", "lessons_pass"})),
    )
)


def test_the_count_engine_matches_the_old_count_due_over_the_whole_grid():
    cfg = Config()
    for tasks, phases, result, replaced in GRID:
        runs = {n: result for n, _k, _u in SV.COUNT_PASSES} if result else {}
        st = _state(tasks, phases, runs)
        assert CA.count_due(st, cfg, replaced=replaced) == _oracle_count_due(st, cfg, replaced), (
            tasks,
            phases,
            result,
            replaced,
        )


def test_a_zero_knob_never_falls_due_and_every_pass_is_in_the_one_table():
    cfg = Config()
    cfg.cadence.integration_tests_every_tasks = 0
    st = _state(50, 50, {})
    assert "integration_tests" not in {d["cadence"] for d in CA.count_due(st, cfg)}
    assert [p.name for p in SV.count_passes(st, cfg)] == [n for n, _k, _u in SV.COUNT_PASSES]


def test_rates_and_due_read_the_same_last_run():
    cfg = Config()
    st = _state(12, 6, {"integration_tests": "7", "lessons_pass": "2"})
    by = {r.name: r for r in RT.cadence_rates(st, cfg)}
    assert by["integration_tests"].at_last == 7 and by["integration_tests"].count == 12
    assert by["lessons_pass"].at_last == 2 and by["lessons_pass"].unit == "phases"
    assert by["dedupe_sweep"].ran == 0 and by["dedupe_sweep"].at_last == 0


def test_a_non_numeric_last_result_reads_as_never_run_not_a_crash():
    """Bug Bb3a7d65b95: `ddflow cadence` raised ValueError on a hand-edited result while
    `doctor`'s rate read the same value as 0. Fails against the unfixed count_due."""
    cfg = Config()
    st = _state(12, 0, {"integration_tests": "abc"})
    due = {d["cadence"]: d for d in CA.count_due(st, cfg)}
    assert due["integration_tests"]["since"] == 12
    assert SV.count_at_last_run(st, "integration_tests") == 0


def test_the_recorded_count_of_a_run_follows_the_one_table(repo):
    """`cadence --ran` writes the completion count of the pass's own unit, derived from
    COUNT_PASSES rather than a second literal list of names (roborev 2205/2206): one done
    task and no done phase, so a task pass records 1 and a phase pass 0."""
    import json

    from conftest import finish, run_cli

    assert run_cli(repo, "init")[0] == 0
    for args in (
        ("phase", "add", "P0", "--title", "p"),
        ("task", "add", "P0.T1", "--phase", "P0", "--globs", "p0/*"),
        ("claim", "P0.T1", "--no-worktree"),
    ):
        assert run_cli(repo, *args)[0] == 0
    assert finish(repo, "P0.T1")[0] == 0
    for name in ("integration_tests", "architecture_review"):
        assert run_cli(repo, "cadence", "--ran", name)[0] == 0
    ran = {}
    for path in (repo / ".ddflow" / "events").glob("*.jsonl"):
        for line in path.read_text().splitlines():
            ev = json.loads(line)
            if ev.get("kind") == "cadence.ran":
                ran[ev["subject"]] = ev["data"]["result"]
    assert ran == {"integration_tests": "1", "architecture_review": "0"}
    assert SV.count_unit("integration_tests") == "tasks"
    assert SV.count_unit("bug_hunt") == ""


# -- the calendar engine (B-uni-triggers.2-calendar-engine) -----------------------------------


def _oracle_calendar_due(st, days, now):
    """`api.operations._calendar_due` before the calendar evaluator moved into `schedule`."""
    due = []
    for name, period in days.items():
        runs = st.cadences.get(name, [])
        last = max((clock.epoch(r["at"], naive="local") for r in runs), default=0.0)
        age_days = (now - last) / 86400 if last else None
        if age_days is None or age_days >= period:
            due.append(
                {
                    "cadence": name,
                    "since": "never"
                    if age_days is None
                    else clock.fmt_age(now - last, "days", places=1),
                    "every": period,
                    "unit": "days",
                }
            )
    return due


T0 = clock.epoch("2026-09-01T00:00:00Z")
DAYS = {"bug_hunt": 7.0, "half": 0.5, "tiny": 0.001, "month": 30.0}
RUNS = (
    {},  # nothing ever ran
    {"bug_hunt": ["2026-09-01T00:00:00Z"]},
    {
        "bug_hunt": ["2026-09-01T00:00:00Z", "2026-08-01T00:00:00Z"]
    },  # newest by timestamp, not order
    {"bug_hunt": ["2026-08-01T00:00:00Z", "2026-09-01T00:00:00Z"]},
    {"half": ["2026-09-01T12:00:00Z"], "month": ["2026-08-02T00:00:00Z"]},
    {"bug_hunt": ["not a time"]},  # an unreadable stamp reads as never ran
)
OFFSETS_S = (0, 60, 43_200, 86_400, 6 * 86_400, 7 * 86_400, 8 * 86_400, 29 * 86_400, 31 * 86_400)


def _calendar_state(runs):
    st = State()
    for name, stamps in runs.items():
        st.cadences[name] = [{"at": at, "result": "0"} for at in stamps]
    return st


def test_the_calendar_engine_matches_the_old_calendar_due_over_the_whole_grid():
    for runs, offset in itertools.product(RUNS, OFFSETS_S):
        st, now = _calendar_state(runs), T0 + offset
        assert SV.calendar_due(st, DAYS, now) == _oracle_calendar_due(st, DAYS, now), (runs, offset)


def test_the_calendar_engine_without_a_clock_uses_the_current_time():
    (row,) = SV.calendar_due(_calendar_state({}), {"x": 1.0})
    assert row == {"cadence": "x", "since": "never", "every": 1.0, "unit": "days"}
    just_now = _calendar_state({"x": [clock.iso_at(time.time(), timespec="seconds")]})
    assert SV.calendar_due(just_now, {"x": 1.0}) == []


def test_a_huge_integer_period_is_a_refusal_not_a_crash_in_a_schedule_file(tmp_path):
    """Bug B51ee2f69f1: tomllib reads an arbitrary-precision integer, and float() of it raised
    OverflowError out of `definitions()`, taking `schedule list` down with one bad file."""
    (tmp_path / ".ddflow" / "schedules").mkdir(parents=True)
    (tmp_path / ".ddflow" / "schedules" / "big.toml").write_text(
        f'title = "Big"\nprompt = "p"\n[cadence]\nevery_days = {10**400}\n'
    )
    defs = SV.definitions(tmp_path, Config(), State())
    assert "big" not in defs.jobs
    assert any("every_days must be a number of days above 0" in e for e in defs.errors), defs.errors


def test_due_cadences_reads_the_calendar_from_config_or_takes_an_explicit_one(tmp_path):
    from ddflow.api.operations import due_cadences

    cfg = Config()
    cfg.cadence.every_days = ["bug_hunt=7"]
    st = _calendar_state({})
    assert [d["cadence"] for d in due_cadences(tmp_path, cfg, st)] == ["bug_hunt"]
    # an explicit calendar overrides the config's, and nothing calendar-based falls due that it
    # does not name
    got = due_cadences(tmp_path, cfg, st, calendar={"other": 1.0})
    assert [d["cadence"] for d in got if d["unit"] == "days"] == ["other"]


def test_one_predicate_decides_what_a_period_in_days_is():
    """`[cadence] every_days` (a `name=days` string) and a schedule's `cadence.every_days` (a
    JSON number) accept exactly the same periods: finite, above zero, not a bool."""
    for bad in (0, -1, math.nan, math.inf, -math.inf, 1e309, 10**400, True, None, "3"):
        assert SV.period_days(bad) is None, bad
        assert SV.normalize({"cadence": {"every_days": bad}}, partial=True)[1], bad
    for text in ("0", "-1", "nan", "inf", "1e309", "x", ""):
        cfg = Config()
        cfg.cadence.every_days = [f"x={text}"]
        try:
            SV.calendar(cfg)
        except ValueError:
            continue
        raise AssertionError(f"calendar accepted {text!r}")
    for good in (1, 0.5, 7, 1e-9):
        assert SV.period_days(good) == float(good)
        assert SV.normalize({"cadence": {"every_days": good}}, partial=True)[1] == []
    cfg = Config()
    cfg.cadence.every_days = ["a=3", "b=0.25"]
    assert SV.calendar(cfg) == {"a": 3.0, "b": 0.25}


# -- the evaluator registry (B-uni-triggers.3-evaluator-registry) ---------------------------


def test_the_registry_runs_the_four_builtins_in_the_order_cadence_always_listed_them():
    assert list(CA.evaluators()) == ["count", "lessons", "export", "calendar"]


def test_due_all_matches_the_old_hand_written_composition_over_the_grid(tmp_path):
    cfg = Config()
    cfg.cadence.every_days = ["dedupe_sweep=7", "bug_hunt=2"]
    days = SV.calendar(cfg)
    for tasks, phases, result, _replaced in GRID:
        runs = {n: result for n, _k, _u in SV.COUNT_PASSES} if result else {}
        st = _state(tasks, phases, runs)
        old = CA.count_due(st, cfg, replaced=set(days))
        old += CA.lessons_cadence(st, cfg)
        old += CA.export_cadence(tmp_path, cfg)
        old += SV.calendar_due(st, days, T0)
        # the calendar evaluator reads the real clock; pin it by comparing the stable parts
        got = CA.due_all(CA.DueContext(tmp_path, cfg, st, days))
        assert [d["cadence"] for d in got] == [d["cadence"] for d in old], (tasks, phases, result)
        assert [d for d in got if d["unit"] != "days"] == [d for d in old if d["unit"] != "days"]


def test_a_new_evaluator_registers_by_name_and_is_listed_last(monkeypatch, tmp_path):
    monkeypatch.setattr(CA, "_EVALUATORS", dict(CA._EVALUATORS))
    seen = []

    def mine(ctx):
        seen.append(ctx.repo)
        return [{"cadence": "mine", "since": 1, "every": 1, "unit": "x"}]

    CA.register_due("mine", mine)
    got = CA.due_all(CA.DueContext(tmp_path, Config(), _state(0, 0, {}), {}))
    assert got[-1]["cadence"] == "mine" and seen == [tmp_path]
    try:
        CA.register_due("mine", mine)
    except ValueError as exc:
        assert "already registered" in str(exc)
    else:
        raise AssertionError("a duplicate name was accepted")
