"""`ddflow` subcommands: history, workflow, help, import, companions, prompts, hooks, mcp and the list viewers.

Registered by `cli.build_parser`, in the order `ddflow --help` lists them."""

from __future__ import annotations

import argparse
from dataclasses import replace

from ..commands.knowledge import cmd_history
from ..commands.operations import cmd_import
from ..commands.setup import cmd_companions, cmd_help, cmd_hooks, cmd_prompts, help_topics
from ..commands.viewers_lists import register as register_list_viewers
from ..commands.workflow import cmd_workflow
from ..declared.hooks import BY_TOOL as HOOKS_BY_TOOL
from ..declared.reporting import BY_TOOL as REPORTING_BY_TOOL
from ..declared.setup import BY_TOOL as SETUP_BY_TOOL
from ..registry import add_commands


def register(s: argparse._SubParsersAction) -> None:
    """Add the workflow subcommands to `s`, the root `ddflow` subparsers."""
    # Handlers that live in `cli.py`; imported at call time, when `cli` is loaded.
    from ..cli import cmd_mcp

    add_commands(s, [REPORTING_BY_TOOL["ddflow_history"]], handlers={("history",): cmd_history})

    wf = s.add_parser(
        "workflow",
        help="the rules this project runs by, and how to change them (exit 1 = incoherent)",
    )
    wf.add_subparsers(dest="workflow_cmd")
    wf.set_defaults(fn=cmd_workflow, dry_run=False)

    add_commands(
        s,
        [
            HOOKS_BY_TOOL[f"ddflow_workflow_{leaf}"]
            for leaf in ("state", "pipeline", "gate", "drop")
        ],
        handlers={
            ("workflow", leaf): cmd_workflow for leaf in ("state", "pipeline", "gate", "drop")
        },
    )

    # The topic names are read here, at parse time, and not at import: see `help_topics`.
    helped = HOOKS_BY_TOOL["ddflow_help"]
    topic = replace(
        helped.params[0],
        cli_help="one of: " + ", ".join(sorted(help_topics())) + ". Omit for the overview.",
    )
    add_commands(s, [replace(helped, params=(topic,))], handlers={("help",): cmd_help})

    add_commands(s, [SETUP_BY_TOOL["ddflow_import"]], handlers={("import",): cmd_import})

    co = s.add_parser(
        "companions",
        help="companion MCP servers that serve the gates (exit 2 = a default one is missing)",
    )
    co_s = co.add_subparsers(dest="companions_cmd")
    co_list = co_s.add_parser("list", help="what is known, installed and registered")
    co_list.add_argument("--no-probe", action="store_true", help="skip the detection probes")
    for _p, _dflt in ((co, False), (co_list, argparse.SUPPRESS)):
        # On the bare `companions` AND on `list`, so `companions --verify` works. The
        # subparser's default is SUPPRESS so it cannot overwrite a value the parent parsed.
        _p.add_argument(
            "--verify",
            action="store_true",
            default=_dflt,
            help="launch each MCP companion and require an answer to `initialize` (spawns "
            "processes; opt-in; exit 1 = one is not an MCP server, 2 = could not tell)",
        )
        _p.add_argument(
            "--id",
            default="" if _dflt is False else argparse.SUPPRESS,
            help="with --verify: comma-separated ids to launch (even if not installed)",
        )
    co_add = co_s.add_parser("add", help="register installed companions in an agent's MCP config")
    # SUPPRESS, as on `list`: the parent already defaults --id to "", and a subparser
    # default would overwrite `companions --id X add` (bug B9969ac4fd9).
    co_add.add_argument(
        "--id", default=argparse.SUPPRESS, help="comma-separated; default: every installed one"
    )
    co_add.add_argument("--agents", default="", help="comma-separated (default: claude)")
    co_add.add_argument(
        "--dry-run",
        action="store_true",
        help="show the exact config entry that would be written, and write nothing",
    )
    co_add.add_argument("--force", action="store_true", help="register one that is not installed")
    co_add.add_argument("--no-probe", action="store_true", help=argparse.SUPPRESS)
    co.set_defaults(fn=cmd_companions, companions_cmd="list", no_probe=False)
    co_list.set_defaults(fn=cmd_companions)
    co_add.set_defaults(fn=cmd_companions)

    pr = s.add_parser("prompts", help="inspect and override the prompt templates")
    pr_s = pr.add_subparsers(dest="prompts_cmd", required=True)
    pr_s.add_parser("list").set_defaults(fn=cmd_prompts)
    prs = pr_s.add_parser("show")
    prs.add_argument("name")
    prs.set_defaults(fn=cmd_prompts)
    prg = pr_s.add_parser(
        "get", help="a workflow command or macro rendered, exactly as MCP prompts/get gives it"
    )
    prg.add_argument("name")
    prg.add_argument(
        "--arg",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="one of its arguments (a macro's params are required); repeat for more",
    )
    prg.set_defaults(fn=cmd_prompts)
    pre = pr_s.add_parser("eject", help="copy the shipped templates into .ddflow/prompts/")
    pre.add_argument("name", nargs="?", default="")
    pre.add_argument("--force", action="store_true")
    pre.set_defaults(fn=cmd_prompts)

    hk = s.add_parser(
        "hooks", help="install/inspect the enforcement git hook and the Claude Code session hook"
    )
    hk_s = hk.add_subparsers(dest="hooks_cmd", required=True)
    hki = hk_s.add_parser("install")
    hki.add_argument(
        "--force",
        action="store_true",
        help="replace an existing pre-commit hook ddflow does not manage",
    )
    _claude_help = (
        "the Claude Code SessionStart hook in .claude/settings.json instead of the git "
        "hook; other hooks there are left exactly as they are"
    )
    hki.add_argument("--claude", action="store_true", help=_claude_help)
    _gemini_help = "the Gemini CLI BeforeAgent prompt hook in .gemini/settings.json"
    _harness_help = (
        "the agent's own hooks, by descriptor id; --harness claude and --harness gemini are "
        "--claude and --gemini (the other agents' hook files arrive with their writers)"
    )
    hki.add_argument("--gemini", action="store_true", help=_gemini_help)
    hki.add_argument("--harness", default="", help=_harness_help)
    hki.set_defaults(fn=cmd_hooks)
    hku = hk_s.add_parser("uninstall")
    hku.add_argument("--claude", action="store_true", help=_claude_help)
    hku.add_argument("--gemini", action="store_true", help=_gemini_help)
    hku.add_argument("--harness", default="", help=_harness_help)
    hku.set_defaults(fn=cmd_hooks)
    hk_s.add_parser("status").set_defaults(fn=cmd_hooks)
    hk_s.add_parser("check-commit", help="(invoked by the hook)").set_defaults(fn=cmd_hooks)
    hkm = hk_s.add_parser("check-msg", help="(invoked by the commit-msg hook)")
    hkm.add_argument("msg_file", help="the message file git passes the hook")
    hkm.set_defaults(fn=cmd_hooks)
    hk_s.add_parser(
        "session-start",
        help="(invoked by the Claude Code SessionStart hook) print the brief; always exit 0",
    ).set_defaults(fn=cmd_hooks)

    hk_s.add_parser(
        "pre-compact",
        help="(invoked by the Claude Code PreCompact hook) record what the session was doing "
        "before compaction; always exit 0",
    ).set_defaults(fn=cmd_hooks)

    hkp = hk_s.add_parser(
        "prompt",
        help="(invoked by the UserPromptSubmit / BeforeAgent hook) record the prompt on stdin; "
        "always exit 0",
    )
    hkp.add_argument("--gemini", action="store_true", help="answer with the JSON Gemini CLI wants")
    hkp.set_defaults(fn=cmd_hooks)

    hkr = hk_s.add_parser(
        "run",
        help="(invoked by any agent's hook) handle one lifecycle event for a harness; "
        "always exit 0",
    )
    hkr.add_argument(
        "hook_event",
        metavar="event",
        nargs="?",
        default="",
        # No `choices`: argparse would exit 2 on an event a newer or older agent config names,
        # and exit 2 BLOCKS in Claude Code and Codex. The handler says it is unknown on stderr.
        help="the lifecycle event: session_start, prompt, pre_tool, post_tool, pre_compact, stop, session_end",
    )
    hkr.add_argument(
        "--harness",
        default="claude",
        help="the agent whose hook dialect the call speaks (a descriptor id; default claude)",
    )
    hkr.set_defaults(fn=cmd_hooks)

    s.add_parser("mcp", help="run the MCP stdio server over this repository").set_defaults(
        fn=cmd_mcp
    )
    register_list_viewers(s)
