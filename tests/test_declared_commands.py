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
from ddflow.surfaces.declared import (
    flow,
    hooks,
    knowledge,
    lifecycle,
    queue,
    records,
    reporting,
    review,
    rules,
    setup,
)
from ddflow.surfaces.declared.answer import ANSWER_PARAMS
from ddflow.surfaces.tools import ADD_TOOLS, TOOLS

FAMILIES = (knowledge, records, queue, lifecycle, rules, review, setup, reporting, hooks, flow)
DECLARED = [c for f in FAMILIES for c in f.COMMANDS]


@pytest.mark.parametrize(
    "entry",
    [
        "ddflow.surfaces.declared.knowledge",
        "ddflow.surfaces.declared.records",
        "ddflow.surfaces.declared.queue",
        "ddflow.surfaces.declared.lifecycle",
        "ddflow.surfaces.declared.rules",
        "ddflow.surfaces.declared.review",
        "ddflow.surfaces.declared.setup",
        "ddflow.surfaces.declared.reporting",
        "ddflow.surfaces.declared.hooks",
        "ddflow.surfaces.declared.flow",
        "ddflow.surfaces.tools.flow",
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


def test_every_add_command_carries_the_duplicate_check_answer():
    adds = [c for c in DECLARED if c.tool in ADD_TOOLS]
    assert {c.tool for c in adds} == {
        "ddflow_lesson_add",
        "ddflow_decision_add",
        "ddflow_bug_found",
        "ddflow_research_add",
        "ddflow_phase_add",
        "ddflow_task_add",
    }
    for command in adds:
        at = command.params.index(ANSWER_PARAMS[0])
        assert command.params[at : at + len(ANSWER_PARAMS)] == ANSWER_PARAMS
        assert list(command.properties())[-2:] == ["relation", "check_only"]


def test_the_answer_flags_exclude_each_other_on_every_add_command():
    parser = build_parser()
    for argv in (
        ["lesson", "add", "--title", "t", "--new", "--extends", "L1"],
        ["decision", "add", "--title", "t", "--decision", "d", "--check", "--new"],
        ["bug", "found", "--summary", "s", "--related", "B1", "--duplicate-of", "B2"],
        ["research", "--question", "q", "--verdict", "THEORETICAL", "--new", "--check"],
        ["phase", "add", "P1", "--new", "--extends", "P0"],
        ["task", "add", "T1", "--related", "T0", "--duplicate-of", "T2"],
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


def test_a_task_lists_its_own_arguments_first_on_the_tool_though_its_flags_follow_the_answer():
    """The order of the flags in `task add --help` and of the tool's properties differ; the
    goldens pin both, this names the one that is easy to lose."""
    props = list(TOOLS["ddflow_task_add"]["properties"])
    assert props[:3] == ["id", "phase", "parent"] and props[-2:] == ["relation", "check_only"]


def test_the_gate_flags_that_differ_by_surface():
    parser = build_parser()
    ns = parser.parse_args(["gate", "record", "I", "G", "--exit-code", "3"])
    assert ns.exit_code == 3 and ns.outcome == "passed"  # an int on the command line...
    assert (
        TOOLS["ddflow_gate_record"]["properties"]["exit_code"][0] == "string"
    )  # ...a string on MCP
    assert TOOLS["ddflow_gate_record"]["properties"]["outcome"][2] is True  # required on MCP only
    assert parser.parse_args(["gate", "skip", "I", "G"]).reason == ""  # optional here...
    assert TOOLS["ddflow_gate_skip"]["properties"]["reason"][2] is True  # ...required there
    assert parser.parse_args(["gate", "status", "I"]).gate == ""  # the dispatcher's default
    with pytest.raises(SystemExit):
        parser.parse_args(["gate", "record", "I", "G", "--outcome", "bogus"])


def test_the_tools_that_need_to_know_where_the_caller_stands_still_say_so():
    asking = {c.tool for c in DECLARED if c.wants_called_from}
    assert asking <= {n for n, spec in TOOLS.items() if spec.get("wants_called_from")}
    assert asking == {
        "ddflow_claim",
        "ddflow_heartbeat",
        "ddflow_gate_run",
        "ddflow_gate_record",
        "ddflow_merge",
        "ddflow_review",
        "ddflow_setup",
    }


def test_the_defaults_the_declarations_read_are_the_ones_the_api_uses():
    from ddflow.api import items
    from ddflow.api import lifecycle as api_lifecycle
    from ddflow.core import defaults

    assert items.DEFAULT_PRIORITY is defaults.DEFAULT_PRIORITY
    assert api_lifecycle.DEFAULT_NEXT_KIND is defaults.DEFAULT_NEXT_KIND
    assert api_lifecycle.DEFAULT_WAIT_TIMEOUT_S is defaults.DEFAULT_WAIT_TIMEOUT_S
    ns = build_parser().parse_args(["task", "add", "T"])
    assert ns.priority == defaults.DEFAULT_PRIORITY
    assert build_parser().parse_args(["next"]).kind == defaults.DEFAULT_NEXT_KIND
    wait = next(c for c in lifecycle.COMMANDS if c.path == ("wait",))
    timeout = next(p for p in wait.params if p.name == "timeout")
    assert str(defaults.DEFAULT_WAIT_TIMEOUT_S) in timeout.cli_help


@pytest.mark.parametrize("tool", ["ddflow_phase_add", "ddflow_task_add"])
def test_a_priority_of_zero_over_mcp_is_filed_as_zero_like_the_command_line(repo, tool):
    """`int(a.get("priority") or DEFAULT)` read a 0 as absent: the tool filed it at 100 while
    `ddflow task add --priority 0` filed it at 0, so the same call ranked differently."""
    import json
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from conftest import run_cli

    out = TOOLS[tool]["api"](repo, {"id": "Z0", "title": "t", "priority": 0}, "agent")
    assert out.exit == 0, out.reason
    code, shown, err = run_cli(repo, "show", "Z0", "--json")
    assert code == 0, err
    assert json.loads(shown)["priority"] == 0
    default = TOOLS[tool]["api"](repo, {"id": "Z1", "title": "t"}, "agent")
    assert default.exit == 0, default.reason
    assert json.loads(run_cli(repo, "show", "Z1", "--json")[1])["priority"] == 100


def test_rule_answers_exclude_each_other_and_a_bare_rule_lists():
    parser = build_parser()
    for argv in (
        ["rule", "add", "--id", "r", "--title", "t", "--new", "--extends", "R1"],
        ["rule", "add", "--id", "r", "--title", "t", "--check", "--related", "R1"],
        ["rule", "edit", "r", "--new", "--related", "R1"],
    ):
        with pytest.raises(SystemExit) as stop:
            parser.parse_args(argv)
        assert stop.value.code == 2, argv
    ns = parser.parse_args(["rule"])
    assert (ns.rule_cmd, ns.tag, ns.scope) == ("list", "", "") and ns.fn.__name__ == "cmd_rule"
    assert parser.parse_args(["rule", "add", "--id", "r", "--title", "t"]).priority is None


def test_the_rule_tools_keep_their_deprecated_arguments_accepted_but_unadvertised():
    for tool, old in (("ddflow_rule_list", {"json", "limit"}), ("ddflow_rule_remove", {"reason"})):
        entry = TOOLS[tool]
        assert set(entry["deprecated"]) == old and old <= set(entry["properties"])
        command = next(c for c in DECLARED if c.tool == tool)
        assert not old & set(command.input_schema()["properties"])


def test_a_rule_priority_of_zero_over_mcp_is_stored_as_zero_like_the_command_line(repo):
    """`int(a.get("priority", 50) or 50)` read a 0 as absent, on add and on edit, while
    `ddflow rule add --priority 0` stored it: the same call ranked a rule differently."""
    add, edit, show = (TOOLS[f"ddflow_rule_{v}"]["api"] for v in ("add", "edit", "show"))
    out = add(repo, {"id": "r-zero", "title": "t", "content": "c", "priority": 0}, "agent")
    assert out.exit == 0, out.reason
    assert show(repo, {"id": "r-zero"}, "agent").data["priority"] == 0
    add(repo, {"id": "r-edit", "title": "u", "content": "d", "priority": 80}, "agent")
    assert edit(repo, {"id": "r-edit", "priority": 0}, "agent").exit == 0
    assert show(repo, {"id": "r-edit"}, "agent").data["priority"] == 0
    add(repo, {"id": "r-dflt", "title": "v", "content": "e"}, "agent")
    assert show(repo, {"id": "r-dflt"}, "agent").data["priority"] == 50


def test_a_rule_search_limit_of_zero_over_mcp_returns_nothing_like_the_command_line(repo):
    """`int(a.get("limit", 10) or 10)` read a 0 as absent and returned ten rows; the command
    line passed the 0 on."""
    add, search = (TOOLS[f"ddflow_rule_{v}"]["api"] for v in ("add", "search"))
    for name, word in (("r-a", "alpha"), ("r-b", "bravo"), ("r-c", "charlie")):
        out = add(repo, {"id": name, "title": f"{word} rule", "content": f"{word} naming"}, "a")
        assert out.exit == 0, out.reason
    assert search(repo, {"query": "naming", "limit": 0}, "a").data["count"] == 0
    assert search(repo, {"query": "naming"}, "a").data["count"] == 3


def test_review_is_one_parser_for_the_run_and_for_triage():
    parser = build_parser()
    run = parser.parse_args(["review", "I", "--gate", "critic", "--chunk", "2", "--chunk", "3,4"])
    assert (run.id, run.chunk, run.finding) == (["I"], ["2", "3,4"], None)
    triage = parser.parse_args(["review", "triage", "I", "--finding", "2", "--refuted"])
    assert (triage.id, triage.finding, triage.refuted) == (["triage", "I"], 2, True)
    assert parser.parse_args(["review"]).id == []  # the handler says what is missing
    entry = TOOLS["ddflow_review"]
    assert entry["wants_called_from"] and entry["wants_progress"] and entry["text"] is True
    assert entry["properties"]["id"][2] is True  # required on the tool, not on the command line
    assert TOOLS["ddflow_review_triage"]["properties"]["probe"][2] is True


def test_verify_takes_an_optional_id_and_the_sweep_flags():
    ns = build_parser().parse_args(["verify", "--all", "--phase", "P", "--limit", "3", "--judge"])
    assert (ns.id, ns.all, ns.phase, ns.limit, ns.judge) == ("", True, "P", 3, True)
    assert "all" not in TOOLS["ddflow_verify"]["properties"]  # a sweep is what omitting id means
    from ddflow.surfaces import exemptions as X

    assert ("ddflow_verify", "--all") in X.FLAG_EXEMPT  # the command carries its own reason
    assert TOOLS["ddflow_verify"]["properties"]["id"][2] is False


def test_the_agent_lists_are_generated_from_the_registry_not_typed():
    from ddflow.services.adopt import AGENT_TARGETS

    listed = ",".join(AGENT_TARGETS)
    assert listed in TOOLS["ddflow_setup"]["properties"]["agents"][1]
    adopt = next(c for c in DECLARED if c.path == ("adopt",))
    assert listed in next(p for p in adopt.params if p.name == "agents").help


def test_config_and_adopt_are_served_by_tools_of_other_names():
    from ddflow.surfaces import exemptions as X

    config = next(c for c in DECLARED if c.path == ("config",))
    assert config.via == ("ddflow_configure",) and not config.tool
    assert X.COVERING_TOOLS["adopt"] == ("ddflow_setup",) and X.COVERING_TOOLS["init"] == (
        "ddflow_setup",
    )
    ns = build_parser().parse_args(["config", "--set", "k", "v", "--local"])
    assert (ns.set, ns.value, ns.local, ns.explain) == ("k", ["v"], True, False)
    assert TOOLS["ddflow_configure"]["properties"]["toml"][0] == "string"  # `--append-toml` there


def test_import_doctor_and_upgrade_keep_their_flags_and_their_tool_exemptions():
    from ddflow.surfaces import exemptions as X

    assert ("ddflow_import", "--verify") in X.FLAG_EXEMPT and (
        "ddflow_doctor",
        "--upgrade",
    ) in X.FLAG_EXEMPT
    assert "ddflow_doctor" in X.PROSE_REASONS and "ddflow_setup" in X.PROSE_REASONS
    ns = build_parser().parse_args(["import", "--apply", "--max-tasks", "5"])
    assert (ns.apply, ns.max_tasks, ns.include_done) == (True, 5, False)
    assert build_parser().parse_args(["doctor", "--upgrade"]).upgrade is True
    up = build_parser().parse_args(["upgrade", "--apply"])  # the hand-written half
    assert up.apply == "all" and "ddflow_upgrade" in TOOLS


def test_the_reporting_commands_keep_the_shapes_the_migration_found():
    """What the declarations carry that a generated parser or tool entry could lose."""
    by = reporting.BY_TOOL
    assert [c.path for c in reporting.COMMANDS] == [
        ("replay",), ("recover",), ("progress",), ("loops",), ("cleanup",), ("rebuild",),
        ("render",), ("board",), ("show",), ("status",), ("history",),
    ]  # fmt: skip
    # `render` is text only with `show`: the table holds the predicate, not a flag
    entry = TOOLS["ddflow_render"]
    assert entry["text"]({"show": "board"}) is True and entry["text"]({}) is False
    assert entry["payload"]({"show": "board"}) == "text" and entry["payload"]({}) == ("files",)
    # `history`: the flag is `--agent`, the argument `by_agent`, the metavar the old one
    parsed = build_parser().parse_args(["history", "--agent", "someone", "--tail", "3"])
    assert (parsed.by_agent, parsed.tail, parsed.limit) == ("someone", 3, 40)
    assert list(TOOLS["ddflow_history"]["properties"]) == [
        "item", "kind", "since", "limit", "tail", "by_agent",
    ]  # fmt: skip
    with pytest.raises(SystemExit):
        build_parser().parse_args(["history", "--tail", "0"])  # positive only, as before
    # `progress` takes its id optionally on the command line and a `limit` only as a tool
    assert build_parser().parse_args(["progress"]).id == ""
    assert "limit" in TOOLS["ddflow_progress"]["properties"]
    assert by["ddflow_board"].prose and by["ddflow_replay"].prose and by["ddflow_render"].prose


def test_the_progress_limit_is_applied_to_the_rows_by_the_mcp_bound():
    """`limit` is not forwarded to the api: the tool's body is the row array and the MCP layer
    cuts it (`mcp_bound.bound_progress`), most effort first; 0 asks for all."""
    from ddflow.surfaces.mcp_bound import BOUNDS

    rows = [{"item": f"T{i}", "seconds": 100 - i} for i in range(40)]
    cut, note = BOUNDS["ddflow_progress"](rows, {"limit": 3})
    assert [r["item"] for r in cut] == ["T0", "T1", "T2"] and note
    default, _ = BOUNDS["ddflow_progress"](rows, {})
    assert len(default) == 25
    everything, _ = BOUNDS["ddflow_progress"](rows, {"limit": 0})
    assert len(everything) == 40
