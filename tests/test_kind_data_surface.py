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
    res = _tool(repo, args=["stakeholder", "add", "s1", "title=Ann", "role=sponsor"])
    assert not res.get("isError"), res
    listed = _body(_tool(repo, args=["stakeholder", "list"]))
    assert [r["id"] for r in listed["rows"]] == ["s1"]
    found = _body(_tool(repo, args=["stakeholder", "search", "@query=sponsor", "@limit=5"]))
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
        assert "ticketx" in D.DEF_KINDS  # a project kind is a definition kind too
    finally:
        R.forget("ticketx")
        D.DEF_KINDS.unregister("ticketx")
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
        D.DEF_KINDS.unregister("goodx")


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
        D.DEF_KINDS.unregister("srch")


def test_a_project_kind_is_seen_by_search_resources_and_a_fresh_cli(repo):
    d = repo / KDA.PROJECT_DIR
    d.mkdir(parents=True)
    (d / "ticketx.toml").write_text(
        'name = "ticketx"\nsummary = "t"\ntext = ["note"]\n[[field]]\nname = "title"\nrequired = true\n'
        '[[field]]\nname = "note"\ndefault = ""\n'
    )
    try:
        # a fresh CLI process (nothing has declared the kind yet) files and finds it
        assert (
            run_cli(repo, "record", "ticketx", "add", "t1", "--set", "title=zebra", agent="a")[0]
            == 0
        )
        code, out, err = run_cli(repo, "search", "--kind", "ticketx", "zebra", agent="a")
        assert code == 0 and "t1" in out, err
        # a fresh MCP server lists its resources
        listed = Server(repo).handle({"jsonrpc": "2.0", "id": 1, "method": "resources/list"})
        assert "ddflow://records/ticketx" in [r["uri"] for r in listed["result"]["resources"]]
    finally:
        R.forget("ticketx")
        D.DEF_KINDS.unregister("ticketx")


@pytest.mark.parametrize(
    ("items", "want"),
    [
        (["@limit=3", "title=t"], {"limit": 3, "set": ["title=t"]}),
        (["@all"], {"all": True, "set": []}),
        (["@where=a=b", "@where=c=d"], {"where": ["a=b", "c=d"], "set": []}),
        (["@relation=extends:x1"], {"relation": "extends:x1", "set": []}),
    ],
)
def test_the_tool_carries_options_as_at_items(items, want):
    got = RK.split_options({"set": items})
    assert {k: got[k] for k in want} == want


def test_a_bad_at_option_is_refused(repo):
    out = RK.call(repo, {"kind": "risk", "verb": "list", "set": ["@nope=1"]}, "a")
    assert out.exit != 0 and "unknown option @nope" in out.reason
    out = RK.call(repo, {"kind": "risk", "verb": "list", "set": ["@limit=x"]}, "a")
    assert out.exit != 0 and "whole number" in out.reason


def test_the_tools_list_stays_under_its_budget_with_the_record_tool():
    reply = Server(".").handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    size = len(json.dumps(reply["result"]["tools"], separators=(",", ":")))
    assert size < 100_000


def test_the_tool_args_are_kind_verb_id_then_fields():
    got = RK.from_args({"args": ["risk", "edit", "r1", "title=t", "@reason=x"]})
    assert (got["kind"], got["verb"], got["id"]) == ("risk", "edit", "r1")
    assert got["set"] == ["title=t", "@reason=x"]
    got = RK.from_args({"args": ["risk", "list", "@limit=2"]})
    assert "id" not in got and got["set"] == ["@limit=2"]


def test_check_only_reaches_dispatch_and_is_refused_where_it_means_nothing(repo):
    add = {"kind": "stakeholder", "verb": "add", "id": "s9", "set": ["title=Ann", "@check_only"]}
    out = RK.call(repo, add, "a")
    assert out.data["check_only"]  # a dry run: nothing was written
    assert RK.call(repo, {"kind": "stakeholder", "verb": "list"}, "a").data["total"] == 0
    edit = {"kind": "stakeholder", "verb": "edit", "id": "s9", "set": ["title=B", "@check_only"]}
    out = RK.call(repo, edit, "a")
    assert out.exit != 0 and "does not take check_only" in out.reason


def test_a_project_override_leaves_the_shipped_definition_kind_alone(repo):
    shipped = D.DEF_KINDS["vendor"]
    d = repo / KDA.PROJECT_DIR
    d.mkdir(parents=True)
    (d / "vendor.toml").write_text(
        'name = "vendor"\nsummary = "our vendor"\n[[field]]\nname = "title"\nrequired = true\n'
    )
    kept = R.KINDS["vendor"]
    try:
        KDA.load_project(repo)
        assert D.DEF_KINDS["vendor"] == shipped  # add_kind never redefines an existing kind
    finally:
        R.forget("vendor")
        R.declare(kept)


def test_the_generic_tool_and_the_generated_commands_share_one_control_list():
    from ddflow.surfaces import records as SR

    assert set(SR.CONTROL_ARGS) <= SR.RESERVED  # a field may not take any of them
    assert {"id", "query", "limit", "check_only"} <= set(SR.CONTROL_ARGS)
