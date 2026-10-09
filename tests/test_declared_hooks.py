"""The workflow, hooks, prompts, companions and help commands, declared once.

The goldens pin `--help` and `tools/list`; these pin what the declarations carry that a
generated parser or tool entry could lose (B-uni-cmd-migrate.6h-hooks).
"""

from __future__ import annotations

import pytest

from ddflow.surfaces.cli import build_parser
from ddflow.surfaces.declared import hooks
from ddflow.surfaces.tools import TOOLS

TOOL_NAMES = [c.tool for c in hooks.COMMANDS if c.tool]


def test_every_declared_tool_is_the_tool_the_table_serves():
    assert len(TOOL_NAMES) == len(set(TOOL_NAMES)) == 11
    for name in TOOL_NAMES:
        assert TOOLS[name]["description"] == hooks.BY_TOOL[name].description


def test_the_workflow_leaves_keep_their_flags_and_defaults():
    parse = build_parser().parse_args
    gate = parse(["workflow", "gate", "g1", "--timeout", "9", "--into", "task", "--required"])
    assert (gate.id, gate.timeout, gate.into, gate.required, gate.dry_run) == (
        "g1", 9, "task", True, False,
    )  # fmt: skip
    assert (gate.command, gate.prompt, gate.cwd, gate.after) == ("", "", "", "")
    assert parse(["workflow", "pipeline", "phase", "a,b", "--dry-run"]).dry_run is True
    assert parse(["workflow", "drop", "g"]).workflow_cmd == "drop"
    assert parse(["workflow"]).workflow_cmd is None  # the bare group shows the rules
    for bad in (["workflow", "pipeline", "both", "a"], ["workflow", "gate", "g", "--cwd", "x"]):
        with pytest.raises(SystemExit):
            parse(bad)


def test_the_tool_schemas_carry_no_enum_the_command_line_has():
    gate = TOOLS["ddflow_workflow_gate"]["properties"]
    assert list(gate) == [
        "id", "command", "prompt", "title", "cwd", "reviewer", "timeout",
        "applies_to", "into", "after", "required", "dry_run",
    ]  # fmt: skip
    assert gate["timeout"][0] == "integer" and gate["id"][2] is True
    declared = hooks.BY_TOOL["ddflow_workflow_gate"]
    assert any(p.choices for p in declared.mcp_params)  # the command line has choices...
    assert all("enum" not in p for p in declared.input_schema()["properties"].values())


def test_help_names_its_topics_on_the_command_line_only():
    from ddflow.services.help import TOPICS

    text = build_parser().parse_args(["help", "gates"]).topic
    assert text == "gates" and build_parser().parse_args(["help"]).topic == ""
    helped = next(a for a in build_parser()._actions if getattr(a, "choices", None))
    assert all(t in helped.choices["help"].format_help() for t in TOPICS)
    assert "one of" not in TOOLS["ddflow_help"]["properties"]["topic"][1]


def test_prompts_is_prose_only_for_the_actions_that_return_text():
    entry = TOOLS["ddflow_prompts"]
    for action, text in (("list", False), ("show", True), ("get", True), ("eject", True)):
        assert entry["text"]({"action": action}) is text
        assert entry["payload"]({"action": action}) == ("text" if text else "rows")
    assert hooks.BY_TOOL["ddflow_prompts"].prose_reason


def test_the_hand_written_command_lines_stay_declared_as_exemptions():
    words = {c.path for c in hooks.COMMANDS if c.reason}
    assert {("hooks", "install"), ("prompts", "get"), ("companions", "add")} <= words
    assert all(c.reason for c in hooks.COMMANDS if not c.tool and c.path)
