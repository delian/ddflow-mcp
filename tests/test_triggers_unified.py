"""One due engine (B-uni-triggers): the count, calendar and registry slices.

The oracle functions below are the PRE-unification implementations, kept verbatim so the
single engine is checked against what the duplicated sites used to compute over a whole grid
of inputs (L-Bf72f9fb741: pin every definition in one table before unifying them).
"""

from __future__ import annotations

import itertools

from ddflow.config import Config
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
