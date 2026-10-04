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

#: EXEMPTION: the `[mcp].tools` tier (core | standard | all) changes what `tools/list`
#: ADVERTISES, never what exists. Every ratchet below reads `TOOLS`, the whole registry,
#: so parity is checked against all tools whatever tier a server runs at; a tool a tier
#: hides is still callable by name. `tests/test_mcp_tool_tiers.py` pins that.

#: CLI command -> the MCP tool(s) that cover it, when the names differ.
ALIASES: dict[str, tuple[str, ...]] = {
    "init": ("ddflow_setup",),
    "adopt": ("ddflow_setup",),
    "config": ("ddflow_configure",),
}

#: CLI commands deliberately NOT exposed, each with its reason.
NOT_EXPOSED: dict[str, str] = {
    "mcp": "starts the MCP server itself; exposing it over MCP would be recursive",
    "search": (
        "people-facing viewer; the consolidated MCP read tool that will carry it is the "
        "later task B-view-mcp-list (tools/list byte budget), until then agents use "
        "`ddflow_recall` and `ddflow_history`"
    ),
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
    ("hooks", "session-start"): (
        "invoked BY the Claude Code SessionStart hook to put the brief into a new "
        "session; over MCP that is ddflow_brief"
    ),
    ("session", "adopt-orphans"): (
        "a one-off backfill an operator runs after ddflow doctor names id-less prompts; "
        "agents record with ddflow_session_prompt, which never lacks a session now"
    ),
    ("hooks", "prompt"): (
        "invoked BY the harness's prompt hook with the prompt's JSON on stdin; an agent "
        "records its own words with ddflow_session_prompt"
    ),
    ("hooks", "check-msg"): (
        "invoked BY the installed commit-msg hook with the message being committed; "
        "it is not something an agent calls"
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
    ("reviewers", "approve"): (
        "a PERSON vouches for a tool-written reviewer (decision D-reviewer-trust); an "
        "agent that could approve the reviewer it wrote would make the record decorative. "
        "test_approve_is_not_an_mcp_tool asserts there is no such tool."
    ),
    ("reviewers", "detect"): "covered by ddflow_reviewers_detect",
    ("reviewers", "list"): "covered by ddflow_reviewers_list",
    ("reviewers", "test"): "covered by ddflow_reviewers_detect, which probes the same way",
    ("adopt",): "covered by ddflow_setup",
    ("init",): "covered by ddflow_setup",
}


#: Read-only viewer leaves served by ONE consolidated tool (a per-leaf tool would cost
#: tools/list bytes for no capability): leaf -> (tool, the `kind` that selects it).
LEAF_VIA: dict[tuple[str, ...], tuple[str, str]] = {
    ("task", "list"): ("ddflow_list", "task"),
    ("phase", "list"): ("ddflow_list", "phase"),
    ("bug", "list"): ("ddflow_list", "bug"),
    ("session", "list"): ("ddflow_list", "session"),
    ("session", "show"): ("ddflow_list", "session"),
    ("search",): ("ddflow_list", "search"),
}


def _tool_stem(path: tuple[str, ...]) -> str:
    """A CLI path as a tool-name stem: `bug file-tasks` -> `bug_file_tasks`. ONE rule for
    both detectors (roborev, job 1296: normalising one and not the other left the flag
    check blind to the hyphenated command)."""
    return "_".join(path).replace("-", "_")


def leaf_covered(path: tuple[str, ...]) -> bool:
    if path in LEAF_NOT_EXPOSED or path in LEAF_VIA:
        return True
    joined = _tool_stem(path)
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
    # `ddflow search` shares ddflow_list with the other viewers, whose `kind` selects the
    # viewer; the search's own `--kind` (which sources) is `sources`, and `--exact` /
    # `--regex` (a mutually exclusive pair) are `mode`.
    ("ddflow_verify", "--all"): "a sweep is what omitting `id` means",
    # TEMPORARY: B-verify-reopen-mcp adds these three properties; mcp.py was leased by B176.
    ("ddflow_verify", "--reopen"): "B-verify-reopen-mcp (mcp.py was leased by B176)",
    ("ddflow_verify", "--reason"): "B-verify-reopen-mcp (mcp.py was leased by B176)",
    ("ddflow_verify", "--force"): "B-verify-reopen-mcp (mcp.py was leased by B176)",
    ("ddflow_list", "--kind"): "carried by `sources`: `kind` selects the viewer",
    ("ddflow_list", "--exact"): "carried by `mode`=exact",
    ("ddflow_list", "--regex"): "carried by `mode`=regex",
    # `ddflow review triage <id>` is the same parser as `ddflow review`: its flags are
    # listed there, and over MCP it is its own tool, `ddflow_review_triage`.
    **{
        (
            "ddflow_review",
            f,
        ): "belongs to `review triage`, which is ddflow_review_triage (plain `review` refuses them)"
        for f in ("--finding", "--refuted", "--confirmed", "--probe")
    },
    # The round budget is the operator's (D-review-budget): an agent may not lift it for
    # an item. `--force --reason` is the recorded per-item exception, CLI only;
    # `ddflow_configure` (reported to the operator) is the MCP route to change the knob.
    **{
        ("ddflow_review", f): "lifting the review-round budget belongs to the operator"
        for f in ("--force", "--reason")
    },
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
    # `export`: the tool's writing is `write=true` + `path`; an agent never overrides hand-edit
    # protection or points the renderer at an arbitrary file (D-export-templates).
    ("ddflow_export", "--update"): "MCP writes with write=true plus a repo-relative path",
    ("ddflow_export", "--out"): "MCP: write=true plus path (the same path-safety rules)",
    (
        "ddflow_export",
        "--force",
    ): "overriding hand-edit protection is the operator's, at a terminal",
    ("ddflow_export", "--template"): "an agent never feeds the renderer an arbitrary file",
    ("ddflow_export", "--lock"): "the operator's veto: a person at a terminal locks a document",
    ("ddflow_export", "--local"): "a per-machine selection is the operator's, at a terminal",
    ("ddflow_export", "--yes"): "answers the terminal confirmation, which MCP has none of",
    # The answer flags of the add-time duplicate check are ONE MCP argument: `relation`
    # ("new", "extends:ID", "duplicate_of:ID", "related:ID" -- a mutually exclusive set
    # is a single string, not four booleans), and `--check` is `check_only`. The pair is
    # on every add tool (tests/test_add_dedupe_mcp.py).
    **{
        (tool, flag): "the duplicate-check answer: MCP `relation` / `check_only`"
        for tool in (
            "ddflow_phase_add",
            "ddflow_task_add",
            "ddflow_bug_found",
            "ddflow_lesson_add",
            "ddflow_decision_add",
            "ddflow_research_add",
            "ddflow_memory_add",
        )
        for flag in ("--new", "--extends", "--duplicate-of", "--related", "--check")
    },
    ("ddflow_bisect", "--glob"): (
        "where candidates come from stays the default tests/**/test_*.py over MCP; an agent "
        "names `candidates` when the suite lives elsewhere (tools/list byte budget)"
    ),
    ("ddflow_bisect", "--repeat"): (
        "re-running each probe is a terminal-side choice for a flaky pollution "
        "(tools/list byte budget)"
    ),
    ("ddflow_bisect", "--max-runs"): (
        "the run budget is the operator's, set at a terminal; the default 200 bounds an "
        "agent's call (tools/list byte budget)"
    ),
}


def _tool_for(path: tuple[str, ...]) -> str | None:
    if path in LEAF_VIA:
        return LEAF_VIA[path][0]
    joined = _tool_stem(path)
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
    "ddflow_lesson_verify": (
        "names the sites a forbidden pattern reappeared at; the list IS the finding, and the "
        "point of B20 is that a caller reads which rather than parsing how many"
    ),
    "ddflow_configure": "prints every knob with its documentation and its source",
    "ddflow_setup": "a checklist of what it wrote and what to do next",
    "ddflow_review": "reviewer findings, already formatted with their severities",
    "ddflow_review_triage": "one confirmation line, which is the whole answer",
    "ddflow_reviewers_list": "a table, plus the warning about unclassified reviewers",
    "ddflow_reviewers_detect": "a probe report naming each endpoint and what answered",
    # Prose for SOME arguments: `--show <view>` returns the rendered document, while
    # `render` alone returns the list of files it wrote. Both are pre-existing contracts.
    #
    # It was missing from this list before the migration, and not because anyone decided
    # it should be: the check built ONE stub argument dict, that stub had no `show` key,
    # so it only ever saw the JSON branch. A tool whose shape depends on its arguments was
    # judged on the arguments the test happened to pass.
    "ddflow_render": "with --show it returns the rendered view itself, to read or commit",
    # Prose for SOME arguments, like `render`: `show` returns the template TEXT and
    # `eject` the list of files it wrote, while `list` is a table callers parse.
    "ddflow_prompts": "with show it returns the template itself, which is the thing to read",
}


def test_every_tool_is_explicitly_json_or_explicitly_prose():
    """Prose is a DECISION, recorded with its reason, never an accident.

    The stub argument dict this used to build is gone with the argv inspection it fed:
    `_returns_prose` reads the declaration instead, which is both cheaper and the thing
    that stayed true when ten tools changed dispatch mechanism.
    """
    undeclared = []
    for name, spec in sorted(TOOLS.items()):
        # Only the string path can be ambiguous about this. A tool on the typed path
        # returns an `Outcome`, whose `data` is structured by construction, and an
        # `identify` tool mutates the connection and returns a sentence -- neither has
        # an argv to inspect, and asking "does its argv say --json" of them would be a
        # KeyError dressed up as a parity finding.
        if "identify" in spec:
            continue
        if not _returns_prose(name) or name in PROSE_TOOLS:
            continue
        undeclared.append(name)
    assert not undeclared, (
        f"{undeclared} return prose but are not in PROSE_TOOLS. Either add `--json` to "
        f"the argv — which is right for anything a caller parses — or record WHY prose "
        f"is the useful form here. An agent cannot tell which it will get."
    )


def _returns_prose(name: str) -> bool:
    """Whether a tool's body is a DOCUMENT, whichever way it is dispatched.

    Two mechanisms express the same fact now: a string-path tool omits `--json` from its
    argv, and a typed tool declares `text`. This test asserted the first only, so a prose
    tool became invisible to it the moment it migrated — with a `KeyError: 'argv'`, which
    at least failed loudly. Asking the question once, here, is what keeps the allowlist
    meaningful across the migration rather than only before it.
    """
    spec = TOOLS[name]
    if "argv" in spec:
        return "--json" not in spec["argv"]({"id": "X", "gate": "g"})
    wants_text = spec.get("text", False)
    if callable(wants_text):
        # A tool that is prose for SOME arguments — `render --show` returns a document,
        # `render` alone returns a file list. Prose is a shape it has.
        return True
    return bool(wants_text)


def test_the_prose_list_only_describes_tools_that_exist():
    stale = [n for n in PROSE_TOOLS if n not in TOOLS]
    assert not stale, f"PROSE_TOOLS names tools that are gone: {stale}"
    for name, reason in PROSE_TOOLS.items():
        assert len(reason) > 20, f"{name}: the reason has to say something"
        assert _returns_prose(name), f"{name} now emits JSON; drop it from PROSE_TOOLS"


def test_every_viewer_leaf_names_a_kind_ddflow_list_accepts():
    from ddflow.api.viewers import READ_KINDS

    for path, (tool, kind) in LEAF_VIA.items():
        assert tool in TOOLS and kind in READ_KINDS, path
