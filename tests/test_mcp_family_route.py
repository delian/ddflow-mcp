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
