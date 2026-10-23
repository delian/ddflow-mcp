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
def test_the_right_name_and_action_reach_the_scenarios_member_on_both_surfaces(scenario):
    family, action = families.placement(scenario.tool)
    member, _args, error = S.resolve("flat", scenario.tool, {})
    assert (member, error) == (scenario.tool, "")
    member, _args, error = S.resolve("families", family, {"action": action} if action else {})
    # a bare action reaches the member, or is refused by THAT action for lack of its arguments
    assert member == scenario.tool or error.startswith(f"{family} action={action}: missing")


def test_a_member_name_is_not_on_the_family_surface():
    member, _args, error = S.resolve("families", "ddflow_gate_record", {"id": "T-1"})
    assert member == "" and "not on this surface" in error
    assert S.resolve("families", "ddflow_claim", {"id": "T-1"})[0] == "ddflow_claim"


def test_a_value_is_carried_as_a_whole_token_not_a_prefix():
    sc = SCENARIOS[2]  # claim T-2
    assert G.grade(sc, "flat", "ddflow_claim", {"id": "T-2"}).args_ok
    assert not G.grade(sc, "flat", "ddflow_claim", {"id": "T-20"}).args_ok
    assert G.grade(sc, "flat", "ddflow_claim", {"ids": ["T-2"]}).args_ok


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
    bad = G.grade(sc, "flat", "ddflow_gate_record", "{not json")
    assert bad.called and not bad.valid
    assert G.tally([bad])["invalid"] == 1 and G.tally([bad])["wrong_tool"] == 0
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
    assert (t["requests"], t["calls"], t["correct"], t["wrong_tool"], t["wrong_args"]) == (
        4,
        3,
        1,
        1,
        1,
    )
    assert t["no_call"] == 1 and t["invalid"] == 0
    assert t["accuracy"] == 0.25
