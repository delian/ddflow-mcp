"""`ddflow metrics suitewaste`: why full-suite runs happen and which share was avoidable."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lanes_fixture import T0, Log

from ddflow.config import Config
from ddflow.core.events import Event
from ddflow.services.perf import suitewaste as SW

CI = {"command": "ddflow ci run", "elapsed_s": 600.0, "waited_s": 60.0}


class Tree:
    """A fake git: sha -> the code tree key (None = git cannot say)."""

    def __init__(self, keys: dict[str, str | None]) -> None:
        self.keys = keys

    def code_tree(self, sha: str) -> str | None:
        return self.keys.get(sha)


def _log(*runs: tuple[str, str, str, dict]) -> list[Event]:
    log = Log()
    for i, (item, gate, kind, ev) in enumerate(runs):
        log.add(f"gate.{kind}", item, "a1", T0 + 100 * (i + 1), gate=gate, by="a1", evidence=ev)
    return list(log.events)


def _ev(sha: str, **more) -> dict:
    return {**CI, "tree_sha": f"{sha}+clean", **more}


def _report(events, keys, **kw):
    return SW.build(events, Config(), T0 + 10_000, Tree(keys), **kw)


def test_two_runs_on_one_code_tree_count_one_avoidable_run():
    ev = _log(("A", "ci", "passed", _ev("aaaaaaa1")), ("B", "ci", "passed", _ev("aaaaaaa2")))
    rep = _report(ev, {"aaaaaaa1": "T", "aaaaaaa2": "T"})
    assert rep["runs"] == 2
    assert rep["avoidable"] == 1
    assert rep["classes"]["first_on_tree"]["runs"] == 1
    assert rep["classes"]["rerun_same_tree_other_item"]["runs"] == 1


def test_same_item_rerun_on_same_tree_is_a_rework_rerun():
    ev = _log(("A", "ci", "failed", _ev("aaaaaaa1")), ("A", "ci", "passed", _ev("aaaaaaa2")))
    rep = _report(ev, {"aaaaaaa1": "T", "aaaaaaa2": "T"})
    assert rep["classes"]["rerun_same_tree_same_item"]["runs"] == 1
    assert rep["avoidable"] == 1


def test_different_code_trees_are_not_avoidable():
    ev = _log(("A", "ci", "passed", _ev("aaaaaaa1")), ("B", "ci", "passed", _ev("aaaaaaa2")))
    rep = _report(ev, {"aaaaaaa1": "T1", "aaaaaaa2": "T2"})
    assert rep["avoidable"] == 0
    assert rep["classes"]["first_on_tree"]["runs"] == 2


def test_events_only_difference_does_not_make_trees_differ():
    # The probe's key ignores .ddflow/events and docs; the same key for two shas = same code.
    ev = _log(("A", "ci", "passed", _ev("aaaaaaa1")), ("A", "ci", "passed", _ev("aaaaaaa2")))
    assert _report(ev, {"aaaaaaa1": "same", "aaaaaaa2": "same"})["avoidable"] == 1


def test_git_code_tree_key_ignores_events_and_docs():
    entries = [("100644", "b1", "ddflow/a.py", 1), ("100644", "e1", ".ddflow/events/x.jsonl", 1)]
    docs = [("100644", "d1", "docs/x.md", 1), ("100644", "d2", "README.md", 1)]
    base = SW.tree_key(entries, Config().gates.doc_globs)
    assert base == SW.tree_key(
        [*entries, *docs, ("100644", "e2", ".ddflow/events/y.jsonl", 1)], Config().gates.doc_globs
    )
    assert base != SW.tree_key([("100644", "b2", "ddflow/a.py", 1)], Config().gates.doc_globs)


def test_unknown_tree_is_unplaced_never_avoidable():
    ev = _log(("A", "ci", "passed", _ev("aaaaaaa1")), ("B", "ci", "passed", _ev("aaaaaaa2")))
    rep = _report(ev, {})
    assert rep["avoidable"] == 0
    assert rep["classes"]["unplaced"]["runs"] == 2


def test_dirty_tree_is_unplaced():
    ev = _log(("A", "ci", "passed", {**CI, "tree_sha": "aaaaaaa1+dirty"}))
    assert _report(ev, {"aaaaaaa1": "T"})["classes"]["unplaced"]["runs"] == 1


def test_run_after_baseline_red_partial_is_classed():
    red = _ev("aaaaaaa1", baseline={"auto": {"accepted": True, "ids": ["t::x"]}})
    ev = _log(("A", "ci", "partial", red), ("A", "ci", "passed", _ev("aaaaaaa2")))
    rep = _report(ev, {"aaaaaaa1": "T1", "aaaaaaa2": "T2"})
    assert rep["classes"]["after_baseline_red"]["runs"] == 1
    assert rep["classes"]["first_on_tree"]["runs"] == 1


def test_minutes_per_class():
    ev = _log(("A", "ci", "passed", _ev("aaaaaaa1")), ("B", "ci", "passed", _ev("aaaaaaa2")))
    cl = _report(ev, {"aaaaaaa1": "T", "aaaaaaa2": "T"})["classes"]
    assert cl["rerun_same_tree_other_item"]["suite_minutes"] == 10.0
    assert cl["rerun_same_tree_other_item"]["queue_minutes"] == 1.0


def test_only_whole_suite_runs_count():
    part = {"command": "uv run pytest -q -n 24 tests/golden/", "tree_sha": "aaaaaaa1+clean"}
    whole = {"command": "uv run pytest tests/ -q --timeout=420 -n 48", "tree_sha": "aaaaaaa2+clean"}
    ev = _log(
        ("A", "unit_tests", "passed", part),
        ("A", "unit_tests", "passed", {"scope": "selected", "tree_sha": "aaaaaaa3+clean"}),
        ("A", "unit_tests", "passed", whole),
        ("A", "review", "passed", _ev("aaaaaaa4")),
    )
    assert _report(ev, {"aaaaaaa2": "T"})["runs"] == 1


def test_no_data_is_unavailable_not_zero():
    rep = _report([], {})
    assert rep["runs"] == 0
    assert rep["avoidable_share"] is None
    assert "avoidable_share" in rep["unavailable"]
    assert "baseline_measure" in rep["unavailable"]


def test_share_and_falsifier_verdict():
    ev = _log(*[(f"I{i}", "ci", "passed", _ev(f"aaaaaaa{i}")) for i in range(4)])
    rep = _report(ev, {f"aaaaaaa{i}": "T" for i in range(4)}, min_samples=2)
    assert rep["avoidable_share"] == 0.75
    assert rep["falsifier"]["threshold"] == 0.25
    assert rep["falsifier"]["verdict"] == "stands"  # 75 percent avoidable: cache-first stands


def test_the_falsifier_refutes_only_when_even_the_upper_bound_is_under_the_threshold():
    one = [("A", "ci", "passed", _ev(f"aaaaaaa{i}")) for i in range(1, 9)]
    keys = {f"aaaaaaa{i}": f"T{i}" for i in range(1, 9)}
    rep = _report(_log(*one), keys, min_samples=1)  # 8 placed, none avoidable
    assert rep["avoidable_share"] == 0.0 and rep["falsifier"]["verdict"] == "refuted"
    more = [*one, *[("B", "ci", "passed", _ev(f"bbbbbbb{i}")) for i in range(4)]]
    rep = _report(_log(*more), keys, min_samples=1)  # 4 unknown of 12 could be avoidable: 33%
    assert rep["avoidable_share_max"] == 4 / 12
    assert rep["falsifier"]["verdict"] == "undetermined"


def test_text_mentions_every_class():
    ev = _log(("A", "ci", "passed", _ev("aaaaaaa1")))
    text = SW.text(_report(ev, {"aaaaaaa1": "T"}, min_samples=1))
    assert "first_on_tree" in text and "avoidable" in text


def test_an_unavailable_or_partial_run_does_not_cover_its_tree():
    ev = _log(
        ("A", "ci", "unavailable", _ev("aaaaaaa1")),
        ("A", "ci", "partial", _ev("aaaaaaa2")),
        ("A", "ci", "passed", _ev("aaaaaaa3")),
    )
    rep = _report(ev, {"aaaaaaa1": "T", "aaaaaaa2": "T", "aaaaaaa3": "T"})
    assert rep["avoidable"] == 0


def test_a_rework_by_the_second_item_is_a_same_item_rerun():
    ev = _log(
        ("B", "ci", "passed", _ev("aaaaaaa1")),
        ("A", "ci", "passed", _ev("aaaaaaa2")),
        ("A", "ci", "passed", _ev("aaaaaaa3")),
    )
    cl = _report(ev, {"aaaaaaa1": "T", "aaaaaaa2": "T", "aaaaaaa3": "T"})["classes"]
    assert cl["rerun_same_tree_other_item"]["runs"] == 1
    assert cl["rerun_same_tree_same_item"]["runs"] == 1


def test_a_phase_scope_reports_only_its_items_but_keeps_the_tree_history():
    ev = _log(("B", "ci", "passed", _ev("aaaaaaa1")), ("A", "ci", "passed", _ev("aaaaaaa2")))
    rep = _report(ev, {"aaaaaaa1": "T", "aaaaaaa2": "T"}, in_scope=lambda i: i == "A")
    assert rep["runs"] == 1
    assert rep["classes"]["rerun_same_tree_other_item"]["runs"] == 1  # B's run still counts


def test_unplaced_runs_do_not_count_in_the_share_but_do_in_the_upper_bound():
    ev = _log(
        ("A", "ci", "passed", _ev("aaaaaaa1")),
        ("A", "ci", "passed", _ev("aaaaaaa2")),
        *[("B", "ci", "passed", _ev(f"bbbbbbb{i}")) for i in range(3)],
    )
    rep = _report(ev, {"aaaaaaa1": "T", "aaaaaaa2": "T"}, min_samples=1)
    assert rep["avoidable_share"] == 0.2
    assert rep["avoidable_share_max"] == 0.8
    assert rep["falsifier"]["verdict"] == "undetermined"


def test_whole_suite_command_detection():
    whole = SW._whole_command
    assert whole("uv run pytest tests/ -q --timeout=420 -n 48")
    assert whole("uv run pytest -q --timeout=420 -n {workers} tests/")
    assert whole("pytest ./tests/")
    assert whole("pytest -n 24 tests")
    assert whole("pytest -n24 --timeout 420 tests")
    assert whole("python -m pytest tests")
    # Anything unrecognised is not guessed whole: a narrowing option, another path, an option
    # whose value is a directory, an unknown flag, no root at all.
    assert not whole("pytest tests -k test_one")
    assert not whole("pytest -vk tests/")
    assert not whole("pytest -mslow2 tests")
    assert not whole("pytest -ktest_foo tests")
    assert not whole("pytest tests/ --ignore=tests/test_slow.py")
    assert not whole("pytest tests --ignore-glob=tests/test_s*.py")
    assert not whole("pytest --ignore tests/")
    assert not whole("pytest --sw-skip tests/")
    assert whole("pytest --ff tests/")  # failed-first only reorders
    assert not whole("pytest -x tests/")  # stops at the first failure
    assert not whole("pytest --maxfail 2 tests/")
    assert not whole("pytest -nfoo tests/")
    assert not whole("pytest test_foo.py --doctest-glob tests")
    assert not whole("pytest test_foo.py --log-file tests")
    assert not whole("pytest --basetemp tests/ tests/test_a.py")
    assert not whole("pytest tests/test_a.py tests/")
    assert not whole("pytest -n 24 tests/golden/")
    assert not whole("pytest -n 24")


def test_a_ci_verdict_without_a_command_is_still_a_suite_run():
    ev = _log(("A", "ci", "passed", {"tree_sha": "aaaaaaa1+clean"}))
    assert _report(ev, {"aaaaaaa1": "T"})["runs"] == 1


def test_a_class_with_an_untimed_run_has_no_total_minutes():
    ev = _log(
        ("A", "ci", "passed", _ev("aaaaaaa1")),
        ("A", "ci", "passed", _ev("aaaaaaa2")),
        ("A", "ci", "passed", {"tree_sha": "aaaaaaa3+clean"}),
    )
    keys = {"aaaaaaa1": "T", "aaaaaaa2": "T", "aaaaaaa3": "T"}
    rep = _report(ev, keys)
    rerun = rep["classes"]["rerun_same_tree_same_item"]
    assert rerun["runs"] == 2
    assert rerun["suite_minutes"] is None  # [600 s, untimed]: a partial sum is not the total
    assert rerun["suite_minutes_timed"] == 10.0
    assert rerun["queue_minutes"] == 1.0  # only the recorded waits are summed
    assert rerun["untimed"] == 1
    assert rep["avoidable_suite_minutes"] is None
    assert "1 untimed" in SW.text(rep)


def test_the_view_scopes_by_phase_through_the_service():
    from metrics_fixture import frozen

    from ddflow.services import metrics as M

    log = Log()
    log.add("phase.added", "Pa", "lead", T0 - 3000, title="a", body="")
    log.add("phase.added", "Pb", "lead", T0 - 3000, title="b", body="")
    log.add("task.added", "A", "lead", T0 - 2000, title="A", body="", parent="Pa")
    log.add("task.added", "B", "lead", T0 - 2000, title="B", body="", parent="Pb")
    for i, item in enumerate(("B", "A")):
        log.add(
            "gate.passed", item, "a1", T0 + 100 * i, gate="ci", by="a1", evidence=_ev("aaaaaaa1")
        )
    st, cfg, now = frozen(log)
    cfg.metrics.min_samples = 1
    probe = Tree({"aaaaaaa1": "T"})
    whole = M.run(log.events, st, cfg, now, view="suitewaste", probes=probe)
    only_a = M.run(log.events, st, cfg, now, view="suitewaste", phase="Pa", probes=probe)
    assert whole.metrics["runs"] == 2
    assert only_a.metrics["runs"] == 1  # phase Pa has A's run only
    assert only_a.metrics["classes"]["rerun_same_tree_other_item"]["runs"] == 1
