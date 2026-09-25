"""Every CLI command must be reachable over MCP.

The requirement: an operator in a chat window — possibly driving a remote agent — must
be able to check status, inspect history and run the workflow without a shell. Any CLI
command with no MCP equivalent is a capability that silently does not exist for them,
and the gap is invisible from either side: the CLI works, the tool list looks full.

This is a ratchet. The exemption list may only SHRINK, and each entry carries the
reason it is not a gap.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ddflow.surfaces.cli import build_parser
from ddflow.surfaces.mcp import TOOLS

#: CLI command -> the MCP tool(s) that cover it, when the names differ.
ALIASES: dict[str, tuple[str, ...]] = {
    "init": ("ddflow_setup",),
    "adopt": ("ddflow_setup",),
    "config": ("ddflow_configure",),
}

#: CLI commands deliberately NOT exposed, each with its reason.
NOT_EXPOSED: dict[str, str] = {
    "mcp": "starts the MCP server itself; exposing it over MCP would be recursive",
    "approve": (
        "clears a HUMAN-approval gate, and the whole point is that the agent cannot. "
        "A human checkpoint reachable from the MCP surface is not a human checkpoint — "
        "it is a second `gate record` with a longer name. This exemption is the "
        "feature, not an oversight, and `test_no_mcp_tool_can_clear_a_human_gate` "
        "asserts it holds end to end rather than resting on this line."
    ),
}


def cli_commands() -> list[str]:
    parser = build_parser()
    action = parser._subparsers._group_actions[0]
    return sorted(action.choices)


def covered(cmd: str) -> bool:
    if cmd in NOT_EXPOSED:
        return True
    for alias in ALIASES.get(cmd, ()):
        if alias in TOOLS:
            return True
    return any(t == f"ddflow_{cmd}" or t.startswith(f"ddflow_{cmd}_") for t in TOOLS)


def cli_leaves() -> list[tuple[str, ...]]:
    """Every terminal subcommand PATH, e.g. ('gate', 'skip'), not just ('gate',).

    The command-level check passed for years while `ddflow gate skip` had no tool at
    all — because `gate` was "covered" by `ddflow_gate_run`. Coverage of a parent says
    nothing about its children, and the child is where the capability lives: `gate
    skip` is the documented escape hatch for `gates.require_outcome`, so an agent
    driving over MCP had no way to drop a single step and had to force past all of them.
    """
    out: list[tuple[str, ...]] = []

    def walk(parser, prefix: tuple[str, ...]) -> None:
        subs = [a for a in parser._actions if hasattr(a, "choices") and a.choices]
        subs = [a for a in subs if hasattr(a, "_name_parser_map") or hasattr(a, "add_parser")]
        if not subs:
            out.append(prefix)
            return
        for action in subs:
            for name, sub in action.choices.items():
                walk(sub, (*prefix, name))

    root = build_parser()
    action = root._subparsers._group_actions[0]
    for name, sub in action.choices.items():
        walk(sub, (name,))
    return sorted(out)


#: Subcommand paths deliberately NOT exposed, each with its reason.
LEAF_NOT_EXPOSED: dict[tuple[str, ...], str] = {
    ("mcp",): "starts the MCP server itself; exposing it over MCP would be recursive",
    ("approve",): (
        "clears a HUMAN-approval gate, and the whole point is that the agent cannot. "
        "See NOT_EXPOSED for the full reason; the property is asserted end to end by "
        "test_no_mcp_tool_can_clear_a_human_gate rather than resting on this line."
    ),
    ("hooks", "status"): "covered by ddflow_hooks, whose action argument selects it",
    ("hooks", "install"): "covered by ddflow_hooks, whose action argument selects it",
    ("hooks", "uninstall"): "covered by ddflow_hooks, whose action argument selects it",
    ("prompts", "list"): "covered by ddflow_prompts, whose action argument selects it",
    ("prompts", "show"): "covered by ddflow_prompts, whose action argument selects it",
    ("prompts", "eject"): "covered by ddflow_prompts, whose action argument selects it",
    ("companions", "list"): "covered by ddflow_companions",
    ("companions", "add"): "covered by ddflow_companions_add",
    ("config",): "covered by ddflow_configure, which reads and writes the same knobs",
    ("decision", "search"): (
        "covered by ddflow_recall, which searches decisions along with everything "
        "else the project remembers — one search beats five"
    ),
    ("hooks", "check-commit"): (
        "invoked BY the installed git hook, inside the commit that is being checked; "
        "it is not something an agent calls"
    ),
    ("reviewers", "presets"): (
        "lists boilerplate for authoring reviewer config, which pairs with "
        "`reviewers add` — an operator edit, exempt for the same reason"
    ),
    ("reviewers", "add"): (
        "writes an API-key env-var name into project config; a config edit an "
        "operator should make deliberately, not an agent mid-task"
    ),
    ("reviewers", "detect"): "covered by ddflow_reviewers_detect",
    ("reviewers", "list"): "covered by ddflow_reviewers_list",
    ("reviewers", "test"): "covered by ddflow_reviewers_detect, which probes the same way",
    ("adopt",): "covered by ddflow_setup",
    ("init",): "covered by ddflow_setup",
}


def leaf_covered(path: tuple[str, ...]) -> bool:
    if path in LEAF_NOT_EXPOSED:
        return True
    joined = "_".join(path)
    return f"ddflow_{joined}" in TOOLS or any(t.startswith(f"ddflow_{joined}_") for t in TOOLS)


def test_every_cli_SUBCOMMAND_is_reachable_over_mcp():
    missing = [p for p in cli_leaves() if not leaf_covered(p)]
    assert not missing, (
        "CLI subcommands with no MCP tool: "
        + ", ".join("`ddflow " + " ".join(p) + "`" for p in missing)
        + ". Add a tool, or add an entry to LEAF_NOT_EXPOSED with the reason."
    )


def test_the_leaf_exemptions_are_real_and_reasoned():
    live = set(cli_leaves())
    stale = [p for p in LEAF_NOT_EXPOSED if p not in live]
    assert not stale, f"exemptions for subcommands that no longer exist: {stale}"
    for path, reason in LEAF_NOT_EXPOSED.items():
        assert len(reason) > 20, f"{path}: the exemption needs a real reason"


def test_the_leaf_detector_can_fail():
    assert not leaf_covered(("definitely", "not", "a", "tool"))


def test_every_cli_command_is_reachable_over_mcp():
    missing = [c for c in cli_commands() if not covered(c)]
    assert not missing, (
        f"CLI commands with no MCP tool: {missing}. An operator in a chat window "
        f"cannot reach these at all. Add a tool, or add an entry to NOT_EXPOSED with "
        f"the reason."
    )


def test_the_exemption_list_only_shrinks():
    stale = [c for c in NOT_EXPOSED if c not in cli_commands()]
    assert not stale, f"exemptions for commands that no longer exist: {stale}"
    for cmd, reason in NOT_EXPOSED.items():
        assert len(reason) > 20, f"{cmd}: the exemption needs a real reason"


def test_the_aliases_all_resolve():
    for cmd, tools in ALIASES.items():
        assert cmd in cli_commands(), f"alias for a command that does not exist: {cmd}"
        for t in tools:
            assert t in TOOLS, f"{cmd} aliases {t}, which is not a tool"


def test_the_detector_can_fail():
    """Planted bad input: a command with no tool must be reported."""
    assert not covered("definitely_not_a_tool_xyzzy")


def test_the_status_and_recall_tools_exist_and_say_what_they_are_for():
    """These two are what an operator asks for in words — 'what is the status', 'have
    we done this before' — so their descriptions have to be recognisable as answers to
    those questions, not as API docs."""
    for name, must in (
        ("ddflow_status", "status of this project"),
        ("ddflow_recall", "HAVE WE BEEN HERE BEFORE"),
    ):
        assert name in TOOLS, name
        assert must.lower() in TOOLS[name]["description"].lower(), name


def test_every_tool_description_is_substantial():
    """The description is the ONLY thing a model sees when deciding whether to call a
    tool. A thin one is a tool that does not get used, or gets used wrongly."""
    thin = [n for n, spec in TOOLS.items() if len(spec["description"]) < 60]
    assert not thin, f"tool descriptions too thin to choose by: {thin}"


# -- flags, not just commands ---------------------------------------------------------


def _cli_flags(argv: list[str]) -> set[str]:
    """Every long option the CLI accepts for one subcommand path."""
    import re as _re
    import subprocess as _sp
    import sys as _sys
    from pathlib import Path as _Path

    root = _Path(__file__).resolve().parents[1]
    p = _sp.run(
        [_sys.executable, "-m", "ddflow", *argv, "--help"],
        capture_output=True,
        text=True,
        env={"PYTHONPATH": str(root), "PATH": os.environ.get("PATH", "")},
        timeout=120,
    )
    return set(_re.findall(r"(--[a-z][a-z0-9-]+)", p.stdout)) - {
        "--help",
        "--json",
        "--repo",
        "--agent",
    }


#: CLI flags deliberately absent from an MCP tool, each with the reason. An entry here
#: is a decision on the record; an omission that is NOT here is a divergence.
FLAG_EXEMPTIONS: dict[tuple[str, str], str] = {
    # `gate skip` shares its argparse parent with `gate record`, so `--help` lists
    # record's evidence flags. They are meaningless for a skip: a skipped gate produced
    # no command, no exit code and no reviewer, which is the whole point of calling it
    # skipped rather than passed. Only `--reason` is real here, and it is required.
    ("ddflow_gate_skip", "--outcome"): "a skip IS the outcome",
    ("ddflow_gate_skip", "--evidence"): "a skipped gate produced none; that is what skipped means",
    ("ddflow_gate_skip", "--command"): "nothing ran",
    ("ddflow_gate_skip", "--exit-code"): "nothing ran",
    ("ddflow_gate_skip", "--output-file"): "nothing ran",
    ("ddflow_gate_skip", "--model"): "no reviewer performed it",
    # `import --verify` is a different QUESTION, not a mode of importing, so it gets
    # its own tool with its own description rather than a boolean on this one. Folding
    # it in would let an agent send `apply=true, verify=true`, which means nothing and
    # would silently do one of them.
    ("ddflow_import", "--verify"): "covered by ddflow_import_verify, its own tool",
}


def _tool_for(path: tuple[str, ...]) -> str | None:
    joined = "_".join(path)
    return f"ddflow_{joined}" if f"ddflow_{joined}" in TOOLS else None


def _pairs() -> list[tuple[str, tuple[str, ...]]]:
    """Every (tool, CLI path) pair, DERIVED rather than listed.

    The first version of this ratchet carried a hand-written list of eleven tools, and
    so was blind to `ddflow remove --force` — which the scenario needed and which
    failed silently until the argument checker started rejecting unknown ones. A
    ratchet with a manually curated input has exactly the coverage someone remembered
    to give it.
    """
    out = []
    for path in cli_leaves():
        if path in LEAF_NOT_EXPOSED:
            continue
        tool = _tool_for(path)
        if tool:
            out.append((tool, path))
    return out


@pytest.mark.parametrize("tool,argv", _pairs(), ids=lambda v: v if isinstance(v, str) else "")
def test_every_cli_flag_is_reachable_from_its_mcp_tool(tool, argv):
    """Command-level parity is not parity.

    The existing ratchet proved every CLI *command* has a tool, and passed while
    `ddflow_phase_add` had no `globs` at all — so over MCP a phase could not declare
    what it writes, the conflict detector had nothing to compare at phase level, and
    (once phase dependencies became real) every task inside it inherited a dependency
    the operator could not scope. An agent driving over MCP had a strictly weaker tool
    than the same agent driving a shell, with nothing saying so.
    """
    from ddflow.surfaces.mcp import TOOLS

    flags = _cli_flags(argv)
    props = set(TOOLS[tool]["properties"])
    missing = sorted(
        f
        for f in flags
        if f.lstrip("-").replace("-", "_") not in props and (tool, f) not in FLAG_EXEMPTIONS
    )
    assert not missing, (
        f"{tool} cannot reach `ddflow {' '.join(argv)}` flag(s) {missing}. "
        f"Add the propert{'y' if len(missing) == 1 else 'ies'} and the argv entry, or "
        f"record the omission in FLAG_EXEMPTIONS with its reason."
    )


# -- JSON or prose, but decided rather than accidental --------------------------------

#: Tools that deliberately return PROSE rather than JSON, each with the reason.
#:
#: The distinction is real and worth keeping: some of these tools exist to hand the
#: model an *instruction* — the next gate's prompt, the decisions in force, the
#: reconstruction narrative — and JSON-encoding a paragraph so the client can decode it
#: again helps nobody. But it was not a decision, it was an accident: `decision_add`
#: returned JSON while `task_add` returned prose, for no reason either could state.
PROSE_TOOLS: dict[str, str] = {
    "ddflow_brief": "a budgeted reading pack — rules, decisions and lessons as text to read",
    "ddflow_board": "a rendered markdown board, meant to be shown or committed as-is",
    "ddflow_gate_status": "carries the next gate's INSTRUCTION, which is the useful half",
    "ddflow_replay": "the reconstruction narrative; the whole output is the deliverable",
    "ddflow_doctor": "a health report written to be read, with remedies in prose",
    "ddflow_configure": "prints every knob with its documentation and its source",
    "ddflow_setup": "a checklist of what it wrote and what to do next",
    "ddflow_review": "reviewer findings, already formatted with their severities",
    "ddflow_reviewers_list": "a table, plus the warning about unclassified reviewers",
    "ddflow_reviewers_detect": "a probe report naming each endpoint and what answered",
}


def test_every_tool_is_explicitly_json_or_explicitly_prose():
    stub = {
        "id": "X",
        "gate": "g",
        "session": "s",
        "text": "t",
        "query": "q",
        "title": "T",
        "decision": "d",
        "summary": "s",
        "into": "a=1,b=2",
        "question": "q",
        "verdict": "CONFIRMED",
        "reason": "r",
        "outcome": "passed",
        "phase": "P",
        "which": "task",
        "gates": "implement,merge",
    }
    undeclared = []
    for name, spec in sorted(TOOLS.items()):
        # Only the string path can be ambiguous about this. A tool on the typed path
        # returns an `Outcome`, whose `data` is structured by construction, and an
        # `identify` tool mutates the connection and returns a sentence -- neither has
        # an argv to inspect, and asking "does its argv say --json" of them would be a
        # KeyError dressed up as a parity finding.
        if "argv" not in spec:
            continue
        argv = spec["argv"](stub)
        if "--json" in argv or name in PROSE_TOOLS:
            continue
        undeclared.append(name)
    assert not undeclared, (
        f"{undeclared} return prose but are not in PROSE_TOOLS. Either add `--json` to "
        f"the argv — which is right for anything a caller parses — or record WHY prose "
        f"is the useful form here. An agent cannot tell which it will get."
    )


def test_the_prose_list_only_describes_tools_that_exist():
    stale = [n for n in PROSE_TOOLS if n not in TOOLS]
    assert not stale, f"PROSE_TOOLS names tools that are gone: {stale}"
    for name, reason in PROSE_TOOLS.items():
        assert len(reason) > 20, f"{name}: the reason has to say something"
        assert "--json" not in TOOLS[name]["argv"]({"id": "X", "gate": "g"}), (
            f"{name} now emits JSON; drop it from PROSE_TOOLS"
        )
