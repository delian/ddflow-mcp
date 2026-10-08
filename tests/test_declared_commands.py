"""Commands declared once (`surfaces/declared/`): the parser and the tool entry are generated.

The goldens (`tests/golden/`) pin every `--help` and every `tools/list` definition byte for
byte; these tests pin what the declarations add: the tool table stays free of `ddflow.api`
whichever side a process enters from, a tool has one declaration, and the shapes the
migration needed (one name for a flag and its argument, a tool that requires what the flag
does not, the duplicate-check answer) behave as before.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from ddflow.surfaces.cli import build_parser
from ddflow.surfaces.declared import knowledge, records
from ddflow.surfaces.declared.answer import ANSWER_PARAMS
from ddflow.surfaces.tools import ADD_TOOLS, TOOLS

FAMILIES = (knowledge, records)
DECLARED = [c for f in FAMILIES for c in f.COMMANDS]


@pytest.mark.parametrize(
    "entry",
    [
        "ddflow.surfaces.declared.knowledge",
        "ddflow.surfaces.declared.records",
        "ddflow.surfaces.tools",
        "ddflow.surfaces.cli",
    ],
)
def test_either_side_of_the_import_cycle_works_and_the_api_stays_unloaded(entry):
    """The declarations use the tool helpers and the tool modules import the declarations."""
    code = f"import sys, {entry}; print('ddflow.api' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == ("True" if entry.endswith("cli") else "False"), out.stderr


def test_a_tool_is_declared_once_and_the_table_serves_the_declaration():
    names = [c.tool for c in DECLARED if c.tool]
    assert len(names) == len(set(names))
    for command in DECLARED:
        if command.tool:
            assert list(TOOLS[command.tool]["properties"]) == list(command.properties())


def test_every_add_command_carries_the_duplicate_check_answer_last():
    adds = [c for c in DECLARED if c.tool in ADD_TOOLS]
    assert {c.tool for c in adds} == {
        "ddflow_lesson_add",
        "ddflow_decision_add",
        "ddflow_bug_found",
        "ddflow_research_add",
    }
    for command in adds:
        assert command.params[-len(ANSWER_PARAMS) :] == ANSWER_PARAMS
        assert list(command.properties())[-2:] == ["relation", "check_only"]


def test_the_answer_flags_exclude_each_other_on_every_add_command():
    parser = build_parser()
    for argv in (
        ["lesson", "add", "--title", "t", "--new", "--extends", "L1"],
        ["decision", "add", "--title", "t", "--decision", "d", "--check", "--new"],
        ["bug", "found", "--summary", "s", "--related", "B1", "--duplicate-of", "B2"],
        ["research", "--question", "q", "--verdict", "THEORETICAL", "--new", "--check"],
    ):
        with pytest.raises(SystemExit) as stop:
            parser.parse_args(argv)
        assert stop.value.code == 2, argv


def test_bug_fixed_skip_flag_and_tool_argument_have_one_name():
    ns = build_parser().parse_args(
        [
            "bug",
            "fixed",
            "B1",
            "--regression-test",
            "t1",
            "--regression-test",
            "t2",
            "--skip-regression-verify",
            "--verify-reason",
            "r",
        ]
    )
    assert (ns.skip_regression_verify, ns.regression_test, ns.verify_reason) == (
        True,
        ["t1", "t2"],
        "r",
    )
    assert "skip_verify" not in vars(ns)
    assert "skip_regression_verify" in TOOLS["ddflow_bug_fixed"]["properties"]


def test_a_note_or_prompt_text_is_optional_on_the_command_line_and_required_on_the_tool():
    parser = build_parser()
    for verb in ("note", "prompt"):
        assert parser.parse_args(["session", verb]).text is None  # read from stdin
        assert TOOLS[f"ddflow_session_{verb}"]["properties"]["text"][2] is True
        assert TOOLS[f"ddflow_session_{verb}"]["properties"]["session"][2] is False


def test_research_keeps_its_optional_verb_and_the_list_form():
    parser = build_parser()
    assert parser.parse_args(["research", "--question", "q", "--verdict", "v"]).verb is None
    assert parser.parse_args(["research", "add", "--question", "q", "--verdict", "v"]).verb == "add"
    assert parser.parse_args(["research", "list"]).verb == "list"  # `viewers_lists` widens it


def test_the_commands_declared_without_a_tool_keep_their_parity_exemption():
    from ddflow.surfaces import exemptions as X

    assert ("decision", "search") in X.EXEMPT_PATHS
    assert ("session", "adopt-orphans") in X.EXEMPT_PATHS
    assert X.ROUTED_PATHS[("bug", "reopen")] == ("ddflow_bug_invalid", "reopen")
    assert X.ROUTED_PATHS[("session", "show")] == ("ddflow_list", "session")
    assert X.ROUTED_PATHS[("search",)] == ("ddflow_list", "search")
    assert "--distinct" in {f for (t, f) in X.FLAG_EXEMPT if t == "ddflow_link"}
    assert "ddflow_lesson_verify" in X.PROSE_REASONS
