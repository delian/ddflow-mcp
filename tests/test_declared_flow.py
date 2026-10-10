"""The flow commands (pr, version, promote, flow) declared once, behave as they did typed out.

The goldens pin every `--help` and `tools/list` entry; these pin the shapes the migration
had to carry: the cut's exact version lands on one name for flag and tool argument, the
lint stays command-line only with its reason, and the groups keep their dispatch words.
"""

from __future__ import annotations

import pytest

from ddflow.surfaces.cli import build_parser
from ddflow.surfaces.declared import flow
from ddflow.surfaces.tools import TOOLS


def _parse(*argv):
    return build_parser().parse_args(list(argv))


def test_every_flow_tool_is_served_by_its_declaration():
    tools = [c for c in flow.COMMANDS if c.tool]
    assert len(tools) == 10
    for command in tools:
        assert list(TOOLS[command.tool]["properties"]) == list(command.properties())


def test_the_exact_version_of_a_cut_is_one_name_on_both_surfaces():
    ns = _parse("version", "cut", "--version", "1.2.3")
    assert ns.version == "1.2.3"
    assert ns.version_cmd == "cut"
    assert "version" in TOOLS["ddflow_version_cut"]["properties"]
    assert _parse("version", "cut").version == ""


def test_the_cut_flags_land_on_the_names_the_handler_reads():
    ns = _parse("version", "cut", "--push", "--dry-run", "--line", "0.1", "--changelog", "--force")
    assert (ns.push, ns.dry_run, ns.line, ns.changelog, ns.force) == (True, True, "0.1", True, True)


def test_a_bump_outside_the_choices_is_refused_and_empty_is_allowed():
    assert _parse("version", "show").bump == ""
    with pytest.raises(SystemExit):
        _parse("version", "show", "--bump", "huge")


def test_the_lint_has_no_tool_and_says_why():
    from ddflow.surfaces import exemptions as X

    lint = next(c for c in flow.COMMANDS if c.path == ("version", "lint"))
    assert not lint.tool and "ddflow_version_cut" in lint.reason
    assert ("version", "lint") in X.EXEMPT_PATHS
    ns = _parse("version", "lint", "--waive", "knob:x", "--reason", "why")
    assert (ns.waive, ns.reason) == ("knob:x", "why")


def test_the_groups_keep_their_dispatch_words_and_run_on_the_executor():
    cases = {
        ("pr", "sync"): "pr_cmd",
        ("pr", "threads", "X"): "pr_cmd",
        ("promote", "status"): "promote_cmd",
        ("flow", "choose", "k", "v"): "flow_cmd",
    }
    for argv, dest in cases.items():
        ns = _parse(*argv)
        assert getattr(ns, dest) == argv[1]
        assert ns.fn.__module__ == "ddflow.surfaces.cliexec"


def test_the_required_positionals_are_required_on_the_tools_too():
    for tool, name in (
        ("ddflow_pr_threads", "id"),
        ("ddflow_promote_add", "env"),
        ("ddflow_promote_deployed", "env"),
        ("ddflow_flow_choose", "knob"),
        ("ddflow_flow_choose", "value"),
    ):
        assert TOOLS[tool]["properties"][name][2] is True
