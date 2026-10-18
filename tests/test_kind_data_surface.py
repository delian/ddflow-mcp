"""SE-kind-data.4: one CLI word and one MCP tool carry every data kind, and a project defines
kinds of its own in .ddflow/kinds/."""

from __future__ import annotations

import json

import pytest
from conftest import run_cli

import ddflow.api.record_kind_data as KDA
import ddflow.api.records as R
import ddflow.core.defs as D
import ddflow.core.kinddata as KD
from ddflow.surfaces import recordkinds as RK
from ddflow.surfaces.mcp import TOOLS, Server


def _tool(repo, **args):
    reply = Server(repo).handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "ddflow_record", "arguments": args},
        }
    )
    return reply["result"]


def _body(res):
    return json.loads(res["content"][0]["text"])


def test_there_is_one_tool_and_no_tool_per_kind():
    assert "ddflow_record" in TOOLS
    assert not [t for t in TOOLS if t in {f"ddflow_{n}" for n in R.KINDS if n != "rule"}]


def test_add_show_edit_list_over_the_cli(repo):
    code, out, err = run_cli(
        repo, "record", "risk", "add", "r1", "--set", "title=Lock-in", "--set", "impact=4",
        agent="a",
    )  # fmt: skip
    assert code == 0, err
    code, out, _ = run_cli(repo, "--json", "record", "risk", "show", "r1", agent="a")
    fields = json.loads(out)["fields"]
    assert fields["impact"] == 4 and fields["state"] == "open"
    assert run_cli(repo, "record", "risk", "edit", "r1", "--set", "state=closed", agent="a")[0] != 0
    code, out, _ = run_cli(repo, "record", "risk", "list", agent="a")
    assert code == 0 and "r1" in out and "Lock-in" in out


def test_the_same_through_the_tool(repo):
    res = _tool(repo, kind="stakeholder", verb="add", id="s1", set=["title=Ann", "role=sponsor"])
    assert not res.get("isError"), res
    listed = _body(_tool(repo, kind="stakeholder", verb="list"))
    assert [r["id"] for r in listed["rows"]] == ["s1"]
    found = _body(_tool(repo, kind="stakeholder", verb="search", query="sponsor"))
    assert found["total"] == 1


@pytest.mark.parametrize(
    ("args", "message"),
    [
        ({"kind": "nope", "verb": "list"}, "unknown record kind 'nope'"),
        ({"kind": "rule", "verb": "list"}, "has commands of its own"),
        ({"kind": "risk", "verb": "merge"}, "verb must be one of"),
        (
            {"kind": "risk", "verb": "add", "id": "x", "set": ["bogus=1"]},
            "risk has no field 'bogus'",
        ),
        ({"kind": "risk", "verb": "add", "id": "x", "set": ["impact"]}, "set takes field=value"),
        ({"kind": "risk", "verb": "add", "id": "x", "set": ["impact=big"]}, "whole number"),
        ({"kind": "risk", "verb": "add", "id": "x", "set": ["title=t", "impact=9"]}, "at most 5"),
    ],
)
def test_malformed_calls_fail_with_a_reason(repo, args, message):
    out = RK.call(repo, args, "a")
    assert out.exit != 0 and message in out.reason


def test_a_project_kind_is_carried_and_can_override_a_shipped_one(repo):
    d = repo / KDA.PROJECT_DIR
    d.mkdir(parents=True)
    (d / "ticketx.toml").write_text(
        'name = "ticketx"\nsummary = "t"\n[[field]]\nname = "title"\nrequired = true\n'
    )
    (d / "vendor.toml").write_text(
        'name = "vendor"\nsummary = "our vendor"\n[[field]]\nname = "title"\nrequired = true\n'
    )
    shipped = R.KINDS["vendor"]
    try:
        assert (
            RK.call(
                repo, {"kind": "ticketx", "verb": "add", "id": "t1", "set": ["title=hi"]}, "a"
            ).exit
            == 0
        )
        assert R.KINDS["vendor"].summary == "our vendor"
    finally:
        R.forget("ticketx")
        D.DEF_KINDS.pop("ticketx", None)
        R.forget("vendor")
        R.declare(shipped)


def test_a_bad_project_kind_names_the_file_and_spares_the_rest(repo):
    d = repo / KDA.PROJECT_DIR
    d.mkdir(parents=True)
    (d / "bad.toml").write_text('name = "bad"\n')
    (d / "good.toml").write_text(
        'name = "goodx"\nsummary = "g"\n[[field]]\nname = "title"\nrequired = true\n'
    )
    try:
        with pytest.raises(KD.KindDataError, match=r"bad\.toml"):
            KDA.load_project(repo)
        assert "goodx" in R.KINDS
        out = RK.call(repo, {"kind": "risk", "verb": "list"}, "a")
        assert out.exit != 0 and "bad.toml" in out.reason
    finally:
        R.forget("goodx")
        D.DEF_KINDS.pop("goodx", None)


def test_no_project_dir_is_no_kinds(repo):
    assert KDA.load_project(repo) == []


def test_a_field_named_like_a_control_argument_is_refused(repo):
    kind = KDA.parse_kind(
        'name = "srch"\nsummary = "s"\n[[field]]\nname = "title"\n[[field]]\nname = "query"\n'
    )
    R.declare(kind)
    try:
        out = RK.call(repo, {"kind": "srch", "verb": "list"}, "a")
        assert out.exit != 0 and "query" in out.reason
    finally:
        R.forget("srch")
        D.DEF_KINDS.pop("srch", None)
