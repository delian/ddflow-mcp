"""The swarm quality ledger: gate outcomes per (family, model, task kind), exploration."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lanes_fixture import T0, Log

from ddflow.config import Config
from ddflow.core.model import fold
from ddflow.services.swarm import ledger as L

H = 3600.0
NOW = T0 + 100 * H


def _item(log, tid, agent, t, outcomes, *, bug_at=None):
    log.task(tid, f"{tid}.py")
    log.claim(agent, tid, t, f"{tid}.py")
    for n, (gate, verdict) in enumerate(outcomes, 1):
        log.add(f"gate.{verdict}", tid, agent, t + n, gate=gate)
    log.complete(agent, tid, t + 100)
    if bug_at is not None:
        log.add("bug.found", f"B{tid}", agent, t + bug_at, item=tid, summary="x")


def _build(log=None):
    log = log or Log()
    log.session("deep", "s1", T0 - 10, tool="opencode", model="deepseek-v4")
    log.session("opus", "s2", T0 - 10, tool="claude-code", model="claude-opus-4")
    return log


def _run(log, **kw):
    st = fold(list(log.events), strict=False)
    return L.build(log.events, st, Config(), NOW, **kw)


def test_cells_keyed_by_family_model_kind_with_rates():
    log = _build()
    clean = [("critic", "passed"), ("ci", "passed")]
    _item(log, "A", "deep", T0, clean)
    _item(log, "B", "deep", T0 + 1000, [("critic", "failed"), ("critic", "passed")])
    _item(log, "C", "deep", T0 + 2000, clean, bug_at=H)
    _item(log, "D", "opus", T0 + 3000, clean)
    led = _run(log)
    cell = led.cells[L.Key("deepseek", "deepseek-v4", "task")]
    assert (cell.items, cell.first_pass, cell.rework, cell.bugs) == (3, 2, 1, 1)
    assert cell.first_pass_rate == 2 / 3
    assert led.cells[L.Key("anthropic", "claude-opus-4", "task")].items == 1
    assert led.cells[L.Key("anthropic", "claude-opus-4", "task")].first_pass_rate == 1.0


def test_unavailable_never_counts_as_passed():
    log = _build()
    _item(log, "A", "deep", T0, [("critic", "unavailable"), ("ci", "passed")])
    cell = _run(log).cells[L.Key("deepseek", "deepseek-v4", "task")]
    assert cell.gates_passed == 1 and cell.gates_unavailable == 1
    assert cell.gate_pass_rate == 0.5  # the unavailable gate is in the denominator only
    assert cell.first_pass == 0 and cell.unverified == 1  # not a clean first pass


def test_late_bug_and_post_completion_failure_are_not_in_window():
    log = _build()
    _item(log, "A", "deep", T0, [("ci", "passed")], bug_at=100 * 3600.0)
    log.add("gate.failed", "A", "deep", T0 + 500, gate="ci")  # after completion
    led = _run(log, bug_window_s=H)
    cell = led.cells[L.Key("deepseek", "deepseek-v4", "task")]
    assert cell.bugs == 0 and cell.rework == 0 and cell.first_pass == 1


def test_free_form_kind_from_tag_and_unknown_author():
    log = _build()
    _item(log, "A", "deep", T0, [("ci", "passed")])
    _item(log, "S", "ghost", T0 + 1000, [("ci", "passed")])
    st = fold(list(log.events), strict=False)
    st.items["A"].tags.append("kind:security-hunt")
    led = L.build(log.events, st, Config(), NOW)
    assert L.Key("deepseek", "deepseek-v4", "security-hunt") in led.cells
    assert L.Key("", "", "task") in led.cells  # no session: unknown, never guessed


def test_smoothed_score_and_prior():
    cell = L.Cell(items=4, first_pass=4)
    assert cell.score() == 5 / 6  # Beta(1,1)
    assert cell.score(prior=(8, 2)) == 12 / 14
    assert L.Cell().score() == 0.5 and L.Cell().first_pass_rate is None


def test_explore_picks_least_sampled_under_threshold_only_on_draw():
    led = L.Ledger(
        {
            L.Key("a", "m1", "task"): L.Cell(items=9),
            L.Key("b", "m2", "task"): L.Cell(items=2),
            L.Key("c", "m3", "task"): L.Cell(items=4),
        }
    )
    cands = [("a", "m1"), ("b", "m2"), ("c", "m3"), ("d", "m4")]
    assert L.explore(led, cands, "task", draw=0.05) == ("d", "m4")  # no data at all
    assert L.explore(led, cands, "task", draw=0.5) is None  # exploit
    assert L.explore(led, cands[:3], "task", draw=0.0) == ("b", "m2")
    well = L.Ledger({L.Key("a", "m1", "task"): L.Cell(items=5)})
    assert L.explore(well, [("a", "m1")], "task", draw=0.0) is None  # nothing under 5
    assert L.explore(led, cands, "task", draw=0.0, share=0.0) is None


def test_to_dict_is_plain_data():
    log = _build()
    _item(log, "A", "deep", T0, [("ci", "passed")])
    d = _run(log).to_dict()
    assert d[0]["family"] == "deepseek" and d[0]["items"] == 1 and "score" in d[0]


def test_author_model_is_the_one_known_at_claim():
    log = _build()
    _item(log, "A", "deep", T0, [("ci", "passed")])
    log.session("deep", "s9", T0 + 50, tool="opencode", model="claude-opus-4")  # after claim
    cells = _run(log).cells
    assert list(cells) == [L.Key("deepseek", "deepseek-v4", "task")]


def test_lease_model_wins_and_equal_time_sessions_keep_log_order():
    log = Log()
    log.session("x", "s1", T0 - 5, tool="t", model="zzz-model")
    log.session("x", "s2", T0 - 5, tool="t", model="claude-opus-4")
    _item(log, "A", "x", T0, [("ci", "passed")])
    assert list(_run(log).cells) == [L.Key("anthropic", "claude-opus-4", "task")]
    log2 = Log()
    log2.task("B", "B.py")
    log2.add("lease.acquired", "B", "y", T0, holder="y", at=T0, model="deepseek-v4", globs=[])
    log2.complete("y", "B", T0 + 9)
    assert list(_run(log2).cells) == [L.Key("deepseek", "deepseek-v4", "task")]
