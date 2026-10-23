"""MTD-family-route: `tools/call` of a family tool runs its member (the engine side of
MTD-family-schema's `family_schema.route`)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.surfaces.mcp import Server


def _call(repo, tool, **args):
    reply = Server(repo).handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool, "arguments": args},
        }
    )
    result = reply["result"]
    return result["content"][0]["text"], bool(result.get("isError"))


@pytest.fixture
def inited(repo):
    run_cli(repo, "init")
    return repo


def test_a_family_call_runs_the_member_and_gives_the_flat_answer(inited):
    fam, fam_err = _call(inited, "ddflow_gate", action="list")
    flat, flat_err = _call(inited, "ddflow_gate_list")
    assert (fam, fam_err) == (flat, flat_err)
    assert not fam_err


def test_a_missing_required_argument_names_the_action(inited):
    text, err = _call(inited, "ddflow_gate", action="record")
    assert err
    assert "ddflow_gate action=record" in text
    assert "missing required argument(s)" in text


def test_an_argument_of_another_action_is_unknown_for_this_one(inited):
    text, err = _call(inited, "ddflow_gate", action="list", gate="critic", bogus="x")
    assert err
    assert "ddflow_gate action=list" in text
    assert "bogus" in text


def test_an_unknown_or_missing_action_is_an_error_naming_the_actions(inited):
    text, err = _call(inited, "ddflow_gate", action="frobnicate")
    assert err and "frobnicate" in text and "record" in text
    text, err = _call(inited, "ddflow_gate")
    assert err and "action" in text and "record" in text


def test_the_legacy_names_and_the_self_named_tools_still_answer_as_before(inited):
    text, err = _call(inited, "ddflow_doctor")
    assert not err
    text2, err2 = _call(inited, "ddflow_doctor", action="check")
    assert not err2
    assert text2 == text
    assert _call(inited, "ddflow_gate_list")[1] is False


def test_a_members_own_action_argument_goes_through_op(inited):
    text, err = _call(inited, "ddflow_gate", action="ci", op="status")
    flat, flat_err = _call(inited, "ddflow_ci", action="status")
    assert (text, err) == (flat, flat_err)


def test_a_non_string_action_is_an_error(inited):
    text, err = _call(inited, "ddflow_gate", action=3)
    assert err and "must be one of" in text


@pytest.mark.parametrize(
    ("name", "args", "offloaded"),
    [
        ("ddflow_gate", {"action": "run"}, True),
        ("ddflow_gate", {"action": "verify"}, True),
        ("ddflow_gate", {"action": "ci"}, True),
        ("ddflow_reviewers", {"action": "review"}, True),
        ("ddflow_lease", {"action": "wait"}, True),
        ("ddflow_doctor", {"action": "verify"}, True),
        ("ddflow_gate", {"action": "list"}, False),
        ("ddflow_gate", {"action": 3}, False),
        ("ddflow_gate", {}, False),
        ("ddflow_gate_run", {}, True),
        ("ddflow_brief", {}, False),
    ],
)
def test_a_family_call_to_a_long_member_runs_in_a_worker(name, args, offloaded):
    from ddflow.surfaces.mcp import _offloaded

    msg = {"method": "tools/call", "id": 1, "params": {"name": name, "arguments": args}}
    assert _offloaded(msg) is offloaded


@pytest.fixture
def old_spelling(monkeypatch):
    from ddflow.surfaces.registry import Alias
    from ddflow.surfaces.tools import TOOLS

    alias = Alias("argument", "since_date", "since", "0.2.3")
    monkeypatch.setitem(TOOLS["ddflow_gate_list"], "arg_aliases", {"since_date": alias})


def test_an_old_argument_spelling_works_through_a_family_call(inited, old_spelling):
    text, err = _call(inited, "ddflow_gate", action="list", refuted=True, since_date="2020-01-01")
    flat, flat_err = _call(inited, "ddflow_gate_list", refuted=True, since="2020-01-01")
    assert (text, err) == (flat, flat_err)


def test_a_clash_between_spellings_names_the_action(inited, old_spelling):
    text, err = _call(inited, "ddflow_gate", action="list", since="a", since_date="b")
    assert err
    assert text.startswith("ddflow_gate action=list: bad arguments: ")
    assert "since_date" in text


def test_ddflow_workflow_without_an_action_is_the_legacy_tool(inited):
    text, err = _call(inited, "ddflow_workflow")
    assert not err
    assert (text, err) == _call(inited, "ddflow_workflow", action="show")


def test_a_required_argument_given_only_under_its_old_spelling_is_given(inited, monkeypatch):
    from ddflow.surfaces.registry import Alias
    from ddflow.surfaces.tools import TOOLS

    spec = TOOLS["ddflow_gate_status"]
    props = dict(spec["properties"])
    assert props["id"][2]
    monkeypatch.setitem(spec, "arg_aliases", {"item": Alias("argument", "item", "id", "0.2.3")})
    fam = _call(inited, "ddflow_gate", action="status", item="T-none")
    assert "missing required" not in fam[0]
    assert fam == _call(inited, "ddflow_gate_status", item="T-none")


def test_a_tools_call_with_no_arguments_object_is_not_offloaded_and_does_not_raise():
    from ddflow.surfaces.mcp import _offloaded

    for params in ({"name": "ddflow_gate"}, {"name": "ddflow_gate", "arguments": None}):
        assert _offloaded({"method": "tools/call", "id": 1, "params": params}) is False
