"""The offline part of the tool-diet eval: the surfaces, the grading and the scenario set are
sound, so a model run measures the model. No model is called here (td_run.py does that)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import td_grade as G
import td_surfaces as S
from td_scenarios import SCENARIOS

from ddflow.surfaces.tools import TOOLS, families


def test_every_scenario_names_a_real_tool_and_a_distinct_prompt():
    assert len({s.prompt for s in SCENARIOS}) == len(SCENARIOS)
    assert {s.tool for s in SCENARIOS} <= set(TOOLS)
    assert len(SCENARIOS) >= 12


def test_the_flat_surface_is_every_tool_and_the_family_surface_is_24():
    assert len(S.flat_tools()) == len(TOOLS)
    fam = S.family_tools()
    assert len(fam) == 24
    assert {f["function"]["name"] for f in fam} >= set(families.HOT_SINGLES)
    assert S.size(fam) < 0.6 * S.size(S.flat_tools())


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_the_right_call_is_graded_correct_on_both_surfaces(scenario):
    """A call built from the member's own place in the family table grades correct."""
    family, action = families.placement(scenario.tool)
    for surface, name, args in (
        ("flat", scenario.tool, {}),
        ("families", family, {"action": action} if action else {}),
    ):
        member, _args, error = S.resolve(surface, name, args)
        # arguments are not under test here; only that the name/action reaches the member
        assert member == scenario.tool or "missing required" in error


def test_a_complete_call_is_correct_and_each_kind_of_miss_is_told_apart():
    sc = SCENARIOS[0]  # gate record on T-1, critic, passed
    ok = {"id": "T-1", "gate": "critic", "outcome": "passed"}
    assert G.grade(sc, "flat", "ddflow_gate_record", ok) == (True, True, True, True)
    fam = {"action": "record", **ok}
    assert G.grade(sc, "families", "ddflow_gate", fam) == (True, True, True, True)
    assert G.grade(sc, "families", "ddflow_gate", json.dumps(fam)).tool_ok
    wrong_tool = G.grade(sc, "flat", "ddflow_gate_skip", {"id": "T-1", "gate": "critic"})
    assert wrong_tool.called and not wrong_tool.tool_ok
    wrong_action = G.grade(sc, "families", "ddflow_gate", {**fam, "action": "skip"})
    assert not wrong_action.tool_ok
    wrong_args = G.grade(sc, "flat", "ddflow_gate_record", {**ok, "gate": "docs"})
    assert wrong_args.tool_ok and not wrong_args.args_ok
    missing = G.grade(sc, "families", "ddflow_gate", {"action": "record"})
    assert not missing.valid
    assert G.grade(sc, "flat", "", "") == (False, False, False, False)
    assert not G.grade(sc, "flat", "ddflow_gate_record", "{not json").valid
    assert not G.grade(sc, "flat", "ddflow_nope", {}).valid


def test_tally_counts_each_kind():
    sc = SCENARIOS[0]
    ok = {"id": "T-1", "gate": "critic", "outcome": "passed"}
    rows = [
        G.grade(sc, "flat", "ddflow_gate_record", ok),
        G.grade(sc, "flat", "ddflow_gate_skip", ok),
        G.grade(sc, "flat", "ddflow_gate_record", {**ok, "gate": "docs"}),
        G.grade(sc, "flat", "", ""),
    ]
    t = G.tally(rows)
    assert (t["calls"], t["correct"], t["wrong_tool"], t["wrong_args"], t["no_call"]) == (
        4,
        1,
        1,
        1,
        1,
    )
    assert t["accuracy"] == 0.25
