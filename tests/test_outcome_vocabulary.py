"""One vocabulary for outcomes (B-uni-outcomes): what "finished" and "live" mean."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from ddflow.config import Config
from ddflow.core.model import (
    ABANDONED,
    BLOCKED,
    DONE,
    GATE_OUTCOMES,
    OPEN,
    REVIEW,
    RUNNING,
    GateRecord,
    Item,
    State,
)

PKG = Path(__file__).resolve().parents[1] / "ddflow"


@pytest.mark.parametrize(
    ("state", "terminal"),
    [
        (OPEN, False),
        (RUNNING, False),
        (BLOCKED, False),
        (REVIEW, False),
        (DONE, True),
        (ABANDONED, True),
    ],
)
def test_terminal_is_done_or_abandoned(state: str, terminal: bool) -> None:
    assert Item(id="x", kind="task", state=state).terminal is terminal


def _state() -> State:
    st = State()
    for n, removed in (("c", False), ("a", True), ("b", False)):
        st.items[n] = Item(id=n, kind="task", removed=removed)
    return st


def test_live_items_skips_removed_and_keeps_definition_order() -> None:
    st = _state()
    assert [i.id for i in st.live_items()] == ["c", "b"]
    assert list(st.live_by_id()) == ["c", "b"]
    assert st.live_by_id()["b"] is st.items["b"]


#: Sites that still spell "done or abandoned" out, awaiting their slice; only goes down.
OLD_TERMINAL_SPELLINGS = 11
OLD_LIVE_COMPREHENSIONS = 7


def _count(pattern: str) -> int:
    rx = re.compile(pattern)
    return sum(len(rx.findall(p.read_text())) for p in PKG.rglob("*.py"))


def test_terminal_spellings_only_go_down() -> None:
    n = _count(r"\(DONE, ABANDONED\)|\(ABANDONED, DONE\)")
    assert n <= OLD_TERMINAL_SPELLINGS, f"{n} sites; use Item.terminal"


def test_live_comprehensions_only_go_down() -> None:
    n = _count(r"for \w+ in \w+\.items\.values\(\) if not \w+\.removed")
    assert n <= OLD_LIVE_COMPREHENSIONS, f"{n} sites; use State.live_items()"


# -- "gate settled": one table across every place that decides it ---------------------
#
# Five call sites each had their own test of whether a gate's outcome is enough:
# `gates.status` (done/current/complete), `completion.verdict` (required gates must
# have PASSED), the brief's "gates remaining" line, `verify._gates` (the same question
# asked of the record), and `api.gates`' "no outcome yet" ordering check.

GATE = "g1"


def _one_gate(outcome: str, *, required: bool) -> tuple[State, Config]:
    cfg = Config.load()
    cfg.gates.task_pipeline = [GATE]
    cfg.gates.required = [GATE] if required else []
    cfg.gates.require_outcome = True
    st = State()
    it = Item(id="T", kind="task")
    if outcome:
        it.gates[GATE] = GateRecord(gate=GATE, outcome=outcome, reason="r")
    st.items["T"] = it
    return st, cfg


def _sites(outcome: str, *, required: bool, repo: Path) -> dict[str, bool]:
    """For each site: does it treat the gate as SATISFIED (nothing left to do for it)?"""
    from ddflow.services import completion
    from ddflow.services import verify as V
    from ddflow.services.gates import status
    from ddflow.views.markdown import _brief_current

    st, cfg = _one_gate(outcome, required=required)
    s = status(st, cfg, "T")
    v = completion.verdict(st, cfg, "T", repo=repo)
    out: list[str] = []
    _brief_current(out, st, cfg, "T", repo)
    led = {
        "gates": {GATE: {"outcome": outcome, "reason": "r"}} if outcome else {},
        "forced": False,
        "overridden": [],
    }
    claim = V._gates(cfg, led, [GATE])
    return {
        "status.done": GATE in s.done,
        "status.complete": s.complete,
        "verdict": not any(GATE in b for b in v.blockers),
        "brief": any("gates remaining: none" in line for line in out),
        "verify": claim.status in {"ok", "warn"},
    }


def _pipeline_ok(outcome: str, required: bool) -> bool:
    """Nothing is left to DO for this gate: passed, or skipped where it is not required."""
    return outcome == "passed" or (outcome == "skipped" and not required)


def _completion_ok(outcome: str, required: bool) -> bool:
    """The gate does not stop the item completing: it has an outcome, and a required gate
    PASSED. A failed gate that is not required is allowed through (D-failed-critic-not-
    blocking), which is why this is not `_pipeline_ok`."""
    return bool(outcome) and (not required or outcome == "passed")


@pytest.mark.parametrize("required", [False, True])
@pytest.mark.parametrize("outcome", ["", *GATE_OUTCOMES])
def test_every_site_agrees_on_a_settled_gate(outcome: str, required: bool, repo: Path) -> None:
    site = _sites(outcome, required=required, repo=repo)
    pipeline = _pipeline_ok(outcome, required)
    completion = _completion_ok(outcome, required)
    assert site == {
        "status.done": pipeline,
        "status.complete": pipeline,
        "brief": pipeline,
        "verdict": completion,
        "verify": completion,
    }


def test_gate_outcome_vocabulary_is_unchanged() -> None:
    """The enum is the old tuple, marks and exits, byte for byte."""
    from ddflow.api.gates import OUTCOME_EXIT
    from ddflow.core.records import OUTCOME_MARK, GateOutcome

    assert GATE_OUTCOMES == ("passed", "failed", "unavailable", "partial", "skipped")
    assert [o.value for o in GateOutcome] == list(GATE_OUTCOMES)
    assert OUTCOME_MARK == {
        "passed": "x",
        "failed": "!",
        "unavailable": "?",
        "partial": "~",
        "skipped": "-",
        "": " ",
    }
    assert OUTCOME_EXIT == {"passed": 0, "skipped": 0, "failed": 1, "unavailable": 2, "partial": 2}


@pytest.mark.parametrize("required", [False, True])
@pytest.mark.parametrize("outcome", ["", "bogus", *GATE_OUTCOMES])
def test_settled_is_satisfies_and_never_for_a_missing_outcome(outcome: str, required: bool) -> None:
    from ddflow.core.records import GateOutcome

    want = outcome == "passed" or (outcome == "skipped" and not required)
    assert GateOutcome.settled(outcome, required) is want
    assert not GateOutcome.settled("", required)
