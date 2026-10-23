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


def _filled(tool):
    """Dummy values for what the member requires, so only the routing is under test."""
    from ddflow.surfaces.tools import family_schema as FS

    return {("action" if n == "op" else n): "x" for n in (FS._requires(tool))}


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_the_right_name_and_action_reach_the_scenarios_member_on_both_surfaces(scenario):
    args = _filled(scenario.tool)
    assert S.resolve("flat", scenario.tool, args) == (scenario.tool, args, "")
    family, action = families.placement(scenario.tool)
    call = {**args, "action": action} if action else args
    if "action" in args and action:  # the member's own action argument is `op` in its family
        call = {**{k: v for k, v in args.items() if k != "action"}, "action": action, "op": "x"}
    member, _args, error = S.resolve("families", family, call)
    assert (member, error) == (scenario.tool, "")


def test_a_member_name_is_not_on_the_family_surface():
    member, _args, error = S.resolve("families", "ddflow_gate_record", {"id": "T-1"})
    assert member == "" and "not on this surface" in error
    assert S.resolve("families", "ddflow_claim", {"id": "T-1"})[0] == "ddflow_claim"


def test_a_value_is_carried_as_a_whole_token_not_a_prefix():
    sc = SCENARIOS[2]  # claim T-2
    assert G.grade(sc, "flat", "ddflow_claim", {"id": "T-2"}).args_ok
    assert not G.grade(sc, "flat", "ddflow_claim", {"id": "T-20"}).args_ok
    assert not G.grade(sc, "flat", "ddflow_claim", {"ids": ["T-2"]}).valid  # not an argument it has
    assert not G.grade(sc, "flat", "ddflow_claim", {"id": "t-2x"}).args_ok
    assert G.grade(
        SCENARIOS[0],
        "flat",
        "ddflow_gate_record",
        {"id": "T-1", "gate": "critic", "outcome": "passed", "reason": "All 40 tests green"},
    ).args_ok
    assert not G.grade(sc, "flat", "ddflow_claim", {"id": "t-2"}).args_ok  # ids are exact


def test_a_complete_call_is_correct_and_each_kind_of_miss_is_told_apart():
    sc = SCENARIOS[0]  # gate record on T-1, critic, passed
    ok = {"id": "T-1", "gate": "critic", "outcome": "passed", "reason": "all 40 tests green"}
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
    ok = {"id": "T-1", "gate": "critic", "outcome": "passed", "reason": "all 40 tests green"}
    rows = [
        G.grade(sc, "flat", "ddflow_gate_record", ok),
        G.grade(sc, "flat", "ddflow_gate_skip", {"id": "T-1", "gate": "critic", "reason": "x"}),
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


def test_every_value_a_scenario_expects_comes_from_its_prompt():
    import re

    for sc in SCENARIOS:
        for v in sc.values:
            assert re.search(re.escape(v), sc.prompt, re.IGNORECASE), (sc.prompt, v)


def test_a_retry_reports_the_second_call_and_its_tokens(monkeypatch):
    import td_run

    calls = []

    def fake_ask(rev, tools, prompt, timeout, after=None):
        calls.append(after)
        return "ddflow_claim", '{"id": "T-2"}', 700

    monkeypatch.setattr(td_run, "ask", fake_ask)
    sc = SCENARIOS[2]
    first = G.grade(sc, "flat", "ddflow_gate_record", {})
    graded, name, _raw, used = td_run.retry(None, [], sc, "flat", "ddflow_complete", "{}", 1, first)
    assert (graded.tool_ok, graded.args_ok, name, used) == (True, True, "ddflow_claim", 700)
    assert calls[0][1]["role"] == "tool"  # the refusal is what the model is shown


def test_the_tagged_surface_tells_a_members_own_action_argument_as_op(monkeypatch):
    props = dict(TOOLS["ddflow_queue"]["properties"])
    props["action"] = ("string", "add|run", True)
    monkeypatch.setitem(TOOLS["ddflow_queue"], "properties", props)
    view = next(f for f in S.hinted_family_tools() if f["function"]["name"] == "ddflow_view")[
        "function"
    ]["parameters"]["properties"]["action"]["description"]
    assert "queue: op" in view and "queue: action" not in view


def test_a_run_reports_the_first_call_and_the_retried_one_apart(monkeypatch):
    import argparse

    import td_run

    answers = iter(
        [
            ("ddflow_gate_record", '{"id": "T-1"}', 100),  # refused: required arguments missing
            ("ddflow_claim", '{"id": "T-2"}', 120),  # the retry is right
        ]
    )
    monkeypatch.setattr(td_run, "ask", lambda *a, **k: next(answers))
    monkeypatch.setattr(td_run, "SCENARIOS", (SCENARIOS[2],))
    args = argparse.Namespace(repeat=1, timeout=1, retry=True)
    out = td_run.run_surface(None, "flat", args)
    assert out["first_call"]["invalid"] == 1 and out["first_call"]["correct"] == 0
    assert out["correct"] == 1 and out["accuracy"] == 1.0
    assert out["prompt_tokens_mean"] == 110  # both asks are counted
    assert [m["retry"] for m in out["misses"]] == [False]
    assert out["misses"][0]["got"] == "ddflow_gate_record"


def test_a_non_object_argument_text_is_a_refusal_not_a_crash(monkeypatch):
    import td_run

    monkeypatch.setattr(td_run, "ask", lambda *a, **k: ("ddflow_claim", '{"id": "T-2"}', 5))
    sc = SCENARIOS[2]
    first = G.grade(sc, "flat", "ddflow_claim", "null")
    assert first.called and not first.valid
    graded, name, _raw, used = td_run.retry(None, [], sc, "flat", "ddflow_claim", "null", 1, first)
    assert graded.valid and name == "ddflow_claim" and used == 5


def test_a_retry_that_never_came_back_adds_no_second_miss(monkeypatch):
    import argparse

    import td_run

    def ask(rev, tools, prompt, timeout, after=None):
        if after:
            raise TimeoutError("no answer")
        return "ddflow_gate_record", '{"id": "T-1"}', 100

    monkeypatch.setattr(td_run, "ask", ask)
    monkeypatch.setattr(td_run, "SCENARIOS", (SCENARIOS[2],))
    out = td_run.run_surface(None, "flat", argparse.Namespace(repeat=1, timeout=1, retry=True))
    assert len(out["misses"]) == 1 and out["misses"][0]["retry"] is False
    assert out["prompt_tokens_mean"] == 100


def test_several_tool_calls_in_one_answer_are_not_a_correct_call(monkeypatch):
    import io
    import json as _json
    import urllib.request

    import td_run

    body = {
        "choices": [
            {
                "message": {
                    "tool_calls": [
                        {"function": {"name": "ddflow_claim", "arguments": '{"id": "T-2"}'}},
                        {"function": {"name": "ddflow_merge", "arguments": '{"id": "T-2"}'}},
                    ]
                }
            }
        ],
        "usage": {"prompt_tokens": 9},
    }
    sent = {}

    def fake_open(req, timeout=0):
        sent.update(_json.loads(req.data))
        return io.BytesIO(_json.dumps(body).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_open)
    rev = type(
        "R", (), {"model": "m", "extra_body": {}, "base_url": "http://x", "api_key": lambda s: ""}
    )()
    name, raw, used = td_run.ask(rev, [], "Claim item T-2", 1)
    assert sent["parallel_tool_calls"] is False
    assert not G.grade(SCENARIOS[2], "flat", name, raw).valid and used == 9
