"""MTD-family-spec: every MCP tool belongs to a family with an action; the family and alias
tables are generated from the declared matrix (`Command.family`/`action`) plus the frozen
legacy placement, never hand-listed per tool (D-mcp-tool-families)."""

from __future__ import annotations

import pytest

from ddflow.surfaces.registry import Command
from ddflow.surfaces.tools import TOOLS
from ddflow.surfaces.tools import families as F

HOT = "identify brief next claim heartbeat complete merge show help".split()


def test_every_tool_has_a_family_and_action():
    placed = {t for fam in F.FAMILIES.values() for t in fam.members.values()}
    assert placed == set(TOOLS)
    for tool in TOOLS:
        fam, action = F.placement(tool)
        assert fam in F.FAMILIES
        assert F.FAMILIES[fam].members[action] == tool


def test_nine_hot_singles_and_fifteen_families_make_24_tools():
    assert F.HOT_SINGLES == tuple("ddflow_" + n for n in HOT)
    singles = [f.name for f in F.FAMILIES.values() if f.single]
    assert singles == [t for t in TOOLS if t in F.HOT_SINGLES]
    assert sorted(singles) == sorted(F.HOT_SINGLES)
    assert all(F.FAMILIES[t].members == {"": t} for t in F.HOT_SINGLES)
    fams = [f for f in F.FAMILIES.values() if not f.single]
    assert len(fams) == 15
    assert len(F.FAMILIES) == 24
    assert sorted(f.name for f in fams) == sorted(
        "ddflow_" + n
        for n in "gate bug decision memory rule session job ship workflow reviewers task lease "
        "view doctor admin".split()
    )


def test_action_is_cli_verb_with_family_noun_stripped():
    assert F.placement("ddflow_gate_record") == ("ddflow_gate", "record")
    assert F.placement("ddflow_bug_found") == ("ddflow_bug", "found")
    assert F.placement("ddflow_lesson_add") == ("ddflow_memory", "lesson_add")
    assert F.placement("ddflow_release") == ("ddflow_lease", "release")
    assert F.placement("ddflow_task_add") == ("ddflow_task", "add")
    assert F.placement("ddflow_brief") == ("ddflow_brief", "")


def test_two_tools_on_one_action_of_a_family_are_refused():
    placed = {
        "ddflow_a": F.Placement("ddflow_gate", "record"),
        "ddflow_b": F.Placement("ddflow_gate", "record"),
    }
    with pytest.raises(RuntimeError, match="record"):
        F.build(placed)


def test_alias_table_is_generated_and_covers_every_non_hot_tool():
    table = {a.old: (a.family, a.action) for a in F.ALIASES}
    assert set(table) == set(TOOLS) - set(F.HOT_SINGLES)
    assert table["ddflow_gate_record"] == ("ddflow_gate", "record")
    assert len(table) == 114


def test_a_declared_family_and_action_win_over_the_legacy_placement():
    c = Command(path=("zed", "go"), tool="ddflow_zed_go", family="ddflow_gate", action="go")
    assert (c.family, c.action) == ("ddflow_gate", "go")
    legacy = {"ddflow_zed_go": F.Placement("ddflow_legacy", "old")}
    placed = F.resolve({"ddflow_zed_go": c}, legacy=legacy)
    assert placed["ddflow_zed_go"] == ("ddflow_gate", "go")


def test_an_unplaced_tool_fails_import_time_resolution():
    c = Command(path=("zed", "go"), tool="ddflow_zed_go")
    with pytest.raises(RuntimeError, match="ddflow_zed_go"):
        F.resolve({"ddflow_zed_go": c}, legacy={})


def test_a_family_with_no_action_but_not_single_is_refused():
    c = Command(path=("zed", "go"), tool="ddflow_zed_go", family="ddflow_gate")
    with pytest.raises(RuntimeError, match="action"):
        F.resolve({"ddflow_zed_go": c}, legacy={})


def test_an_action_without_a_family_is_refused_even_when_the_legacy_table_knows_the_tool():
    c = Command(path=("zed", "go"), tool="ddflow_zed_go", action="go")
    legacy = {"ddflow_zed_go": F.Placement("ddflow_gate", "go")}
    with pytest.raises(RuntimeError, match="ddflow_zed_go"):
        F.resolve({"ddflow_zed_go": c}, legacy=legacy)


def test_a_declared_family_may_not_be_a_hot_single():
    c = Command(path=("zed", "go"), tool="ddflow_zed_go", family="ddflow_identify", action="go")
    with pytest.raises(RuntimeError, match="hot single"):
        F.resolve({"ddflow_zed_go": c}, legacy={})
