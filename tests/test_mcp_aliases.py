"""MTD-aliases: every old tool name that is not a hot single is a hidden `Alias` of its family
action: generated from the family table, callable, noted once, and pinned by a golden."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conftest import run_cli

from ddflow.surfaces import registry as R
from ddflow.surfaces.mcp import TOOLS, Server
from ddflow.surfaces.tools import aliases as A
from ddflow.surfaces.tools import families as F

GOLDEN = Path(__file__).parent / "data" / "mcp_tool_aliases.json"


def test_one_alias_per_old_name_that_is_not_a_hot_single():
    assert set(A.ALIASES) == set(TOOLS) - set(F.HOT_SINGLES)
    assert len(A.ALIASES) == 114
    for old, alias in A.ALIASES.items():
        assert isinstance(alias, R.Alias)
        assert (alias.kind, alias.old) == ("tool", old)


def test_each_alias_says_the_family_and_action_and_keeps_the_name_until_1_0():
    for old, alias in A.ALIASES.items():
        family, action = F.placement(old)
        assert family in alias.new and f"action={action}" in alias.new
        assert alias.since == A.SINCE
        assert alias.removed_in == "1.0"
        assert f"'{old}'" in alias.notice()


def test_hot_singles_unknown_names_and_the_two_self_named_tools_are_not_noted():
    for name in ("ddflow_brief", "ddflow_nope", "ddflow_doctor", "ddflow_workflow"):
        assert A.alias_for(name) is None
    assert "ddflow_doctor" in A.ALIASES  # still in the table: it is a member of its family
    assert A.alias_for("ddflow_gate_record") is A.ALIASES["ddflow_gate_record"]


def test_the_notice_is_said_once_per_session():
    notices = R.Notices()
    first = notices.fresh([A.alias_for("ddflow_gate_record")])
    again = notices.fresh([A.alias_for("ddflow_gate_record")])
    assert len(first) == 1 and again == []


def test_the_golden_alias_table_is_unchanged():
    got = {old: [a.new, a.since, a.removed_in] for old, a in sorted(A.ALIASES.items())}
    if os.environ.get("DDFLOW_UPDATE_GOLDEN"):
        GOLDEN.write_text(json.dumps(got, indent=1, sort_keys=True) + "\n")
    assert json.loads(GOLDEN.read_text()) == got


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


@pytest.mark.parametrize(
    "old, args",
    [
        ("ddflow_gate_list", {}),
        ("ddflow_rule_list", {}),
        ("ddflow_status", {}),
        ("ddflow_doctor", {}),
    ],
)
def test_an_old_name_still_answers(repo, old, args):
    run_cli(repo, "init")
    answer = _call(repo, old, **args)
    assert not answer[1], answer
