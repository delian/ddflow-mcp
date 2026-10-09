"""The workflow, hooks, prompts, companions and help commands, declared once.

`surfaces/parsers/workflow.py` registers the command-line halves that are regular (the
`workflow` leaves and `help`); the tools of all of them come from here (D-unify 4,
B-uni-cmd-migrate.6h-hooks). `hooks`, `prompts` and `companions` select their operation by an
`action` argument or a subcommand with its own flag quirks, so their command lines stay
hand-written and are declared here as the exemptions they are.
"""

from __future__ import annotations

from ..registry import Command, Param, by_tool
from ..tools._common import _all_tools, _api

_COVERED = "covered by {}, whose action argument selects it"

COMMANDS: tuple[Command, ...] = (
    Command(
        path=(),
        tool="ddflow_workflow",
        description="The rules THIS project runs by, in one answer: the gates every task and phase passes in order, the completion rules, parallelism caps, reviewers, and where each value came from. Call it before your first `ddflow_claim` and after any workflow change: instructions are computed once at start. Exit 1: the workflow does not hang together (e.g. a pipeline names a gate with no definition). Read-only.",
        call=lambda repo, a, agent: _api().workflow_show(repo),
        # The whole `data`: `ddflow workflow --json` has always emitted one object
        # with every section in it, and callers read `coherent` and `findings`.
        payload="",
    ),
    Command(
        path=("workflow", "state"),
        tool="ddflow_workflow_state",
        summary="one-page overview: workflow, rules, queue, bugs, diagram",
        description="One-call project overview: workflow, rules, decisions, active work, queue and bugs with names and priorities, blockers, and a Mermaid gate diagram. Read-only.",
        call=lambda repo, a, agent: _api().workflow_overview(repo, agent),
        payload=(
            "workflow",
            "workflow_diagram",
            "rules",
            "decisions",
            "active_work",
            "project",
            "task_queue",
            "bugs",
            "blockers",
            "discovery_hints",
        ),
    ),
    Command(
        path=("workflow", "pipeline"),
        tool="ddflow_workflow_pipeline",
        summary="set the gates a task or phase passes through",
        description="Set the ordered list of gates a task or a phase must pass. WRITES this project's config. Validated first: a gate id with no definition is REFUSED (it would block every item that reaches it); define it with `ddflow_workflow_gate`. Ask the operator before changing a pipeline -- it governs every future item, and removing a gate removes a check somebody added on purpose. dry_run shows what it would do.",
        params=(
            Param(
                "which",
                help="'task' or 'phase'.",
                positional=True,
                choices=("task", "phase"),
                cli_help="",
            ),
            Param(
                "gates",
                help="Comma-separated gate ids, in the order they run.",
                positional=True,
                cli_help="comma-separated gate ids, in order",
            ),
            Param(
                "dry_run",
                type="boolean",
                help="Report the change and write nothing.",
                cli_help="show it; write nothing",
            ),
        ),
        call=lambda repo, a, agent: _api().workflow_pipeline(
            repo, a["which"], a["gates"], dry_run=bool(a.get("dry_run")), agent=agent
        ),
        payload=("key", "gates", "applied"),
    ),
    Command(
        path=("workflow", "gate"),
        tool="ddflow_workflow_gate",
        summary="define or change one gate",
        description="Define or change one gate, optionally in a pipeline. WRITES the project config. `command` makes a COMMAND gate (ddflow runs it; its exit code is the evidence); `prompt` makes an AGENT gate (you perform and record it); one is required. `into` adds it to a pipeline (`after` places it, default last); `required` blocks completion without it. Ask the operator first; prefer dry_run.",
        params=(
            Param(
                "id",
                help="The gate id, e.g. 'lint' or 'security_scan'.",
                positional=True,
                cli_help="",
            ),
            Param(
                "command",
                help="Shell command to run. Makes it a command gate.",
                default="",
                cli_help="what to run; makes it a command gate",
            ),
            Param(
                "prompt",
                help="What an agent must do. Makes it an agent gate.",
                default="",
                cli_help="what to ask an agent; makes it an agent gate",
            ),
            Param("title", help="Human-readable name.", default="", cli_help=""),
            Param(
                "cwd",
                help="'worktree' (default) or 'repo'.",
                default="",
                choices=("", "worktree", "repo"),
                cli_help="",
            ),
            Param(
                "reviewer",
                help="'different_family' to require a reviewer from another model family, or 'same_family_ok'.",
                default="",
                choices=("", "different_family", "same_family_ok"),
                cli_help="require a reviewer, and whether it must be a different model family",
            ),
            Param(
                "timeout",
                type="integer",
                help="Seconds before the command counts as unavailable.",
                default=0,
                cli_help="seconds before it is unavailable",
            ),
            Param(
                "applies_to",
                help="'task', 'phase' or 'both'.",
                default="",
                choices=("", "task", "phase", "both"),
                cli_help="",
            ),
            Param(
                "into",
                help="Add to the 'task', 'phase' or 'both' pipeline(s).",
                default="",
                choices=("", "task", "phase", "both"),
                cli_help="add to a pipeline",
            ),
            Param(
                "after",
                help="Place it after this gate. Default: last.",
                default="",
                cli_help="place it after this gate (default: last)",
            ),
            Param(
                "required",
                type="boolean",
                help="An item cannot complete without it.",
                cli_help="an item cannot complete without it",
            ),
            Param(
                "dry_run",
                type="boolean",
                help="Report the change and write nothing.",
                cli_help="show it; write nothing",
            ),
        ),
        call=lambda repo, a, agent: _api().workflow_gate(
            repo,
            _api().WorkflowGateEdit(
                id=a["id"],
                command=a.get("command", "") or "",
                prompt=a.get("prompt", "") or "",
                cwd=a.get("cwd", "") or "",
                reviewer=a.get("reviewer", "") or "",
                title=a.get("title", "") or "",
                timeout=int(a.get("timeout") or 0),
                applies_to=a.get("applies_to", "") or "",
                into=a.get("into", "") or "",
                after=a.get("after", "") or "",
                required=bool(a.get("required")),
            ),
            dry_run=bool(a.get("dry_run")),
            agent=agent,
        ),
        payload=("gate", "changed", "applied"),
    ),
    Command(
        path=("workflow", "drop"),
        tool="ddflow_workflow_drop",
        summary="take a gate out of the pipelines (its definition stays)",
        description="Take a gate out of every pipeline -- task, phase and promotion -- and out of `required`, so it does not become a requirement that requires nothing. WRITES config. The gate's DEFINITION stays, so putting it back is one call. Exit 2: it was in no pipeline. Ask the operator first: a gate in a pipeline is a check somebody added on purpose.",
        params=(
            Param(
                "id", help="The gate id to remove from the pipelines.", positional=True, cli_help=""
            ),
            Param(
                "dry_run",
                type="boolean",
                help="Report the change and write nothing.",
                cli_help="show it; write nothing",
            ),
        ),
        call=lambda repo, a, agent: _api().workflow_drop(
            repo, a["id"], dry_run=bool(a.get("dry_run")), agent=agent
        ),
        payload=("gate", "removed_from", "applied"),
    ),
    Command(
        path=("help",),
        tool="ddflow_help",
        summary="what ddflow is, what it can do, and the workflow (try: ddflow help workflow)",
        description="What ddflow IS, what it can do, and what the workflow is. Call this first if you have not used it before: the other descriptions explain one tool each and the connection instructions describe THIS repository; neither answers 'how am I meant to work here'. No argument: the loop from picking work to landing it, the exit codes, and every capability grouped by purpose. `topic`: workflow, import, gates, parallel, memory, recovery, config. Read-only; the pages are templates a project may override.",
        params=(
            Param(
                "topic",
                help="workflow | import | gates | parallel | memory | recovery | config. Omit for the overview, which lists them.",
                positional=True,
                nargs="?",
                default="",
                # The parser fills in the topic names (`help_topics`), read lazily.
                cli_help="one of the help topics. Omit for the overview.",
            ),
        ),
        call=lambda repo, a, agent: _api().help_topic(
            repo, topic=a.get("topic", "") or "", tools=_all_tools(), agent=agent
        ),
        payload=("topic", "text", "topics"),
    ),
    Command(
        path=(),
        tool="ddflow_prompts",
        description="Inspect the prompt templates this project uses and where each comes from (shipped, project override, or config path). `get` renders a workflow command or macro as prompts/get does; `eject` copies the shipped ones into .ddflow/prompts/ to edit as plain text.",
        params=(
            Param("action", help="list (default), show, get, or eject."),
            Param("name", help="Template name."),
            Param("arg", type="array", help="get: KEY=VALUE."),
        ),
        call=lambda repo, a, agent: _api().prompts(
            repo,
            action=a.get("action", "list") or "list",
            name=a.get("name", "") or "",
            agent=agent,
            # One KEY=VALUE sent as a bare string is one argument, not one per character.
            arg=[a["arg"]] if isinstance(a.get("arg"), str) else list(a.get("arg") or []),
        ),
        # Prose for SOME arguments, like `render`: `show`/`get` return the prompt TEXT and
        # `eject` the files it wrote, while `list` is a table callers parse.
        payload=lambda a: "text" if a.get("action") in ("show", "get", "eject") else "rows",
        prose=lambda a: a.get("action") in ("show", "get", "eject"),
        prose_reason="with show it returns the template itself, which is the thing to read",
        kind="prompts",
    ),
    Command(
        path=(),
        tool="ddflow_hooks",
        description="Inspect or install the enforcement git hook — the one layer of this workflow that does not depend on the agent agreeing. It refuses a commit touching paths no live lease of yours covers. `status` reports whether it is installed AND whether the policy actually blocks, since a block policy with no hook installed enforces nothing.",
        params=(
            Param("action", help="status (default), install, uninstall."),
            Param(
                "claude",
                type="boolean",
                help="Install/uninstall the Claude Code SessionStart hook in .claude/settings.json instead of the git hook: every session, even after compaction, starts with the ddflow brief. Other hooks there are untouched.",
            ),
        ),
        call=lambda repo, a, agent: _api().hooks(
            repo,
            action=a.get("action", "status") or "status",
            claude=bool(a.get("claude")),
            agent=agent,
        ),
        # The FACTS. `message` is the prose rendering of them and stays OUT of the JSON
        # body. `session_hook` was ADDED deliberately with the SessionStart hook -- a
        # wire change of its own, not part of the migration this comment once guarded
        # -- and is `null` when the settings file could not be read.
        payload=("installed", "policy", "session_hook", "trailer_hook"),
    ),
    Command(
        path=(),
        tool="ddflow_companions",
        description="Which companion MCP servers serve this project's gates, which are installed, which an agent launches. Without them, agent gates pass on assertion. Exit 2: a default one is missing or unregistered. Read-only; never installs or launches.",
        params=(
            Param(
                "no_probe", type="boolean", help="Skip the detection probes (faster, less certain)."
            ),
        ),
        call=lambda repo, a, agent: _api().companions_list(
            repo, no_probe=bool(a.get("no_probe")), agent=agent
        ),
        payload=("companions", "gate_coverage", "uncovered_gates"),
    ),
    Command(
        path=(),
        tool="ddflow_companions_verify",
        description="Launch MCP companions and require a JSON-RPC answer to `initialize`. SPAWNS processes (opt-in). Default: registered or installed ones. Exit 1: not an MCP server; 2: unsure.",
        params=(Param("id", help="Comma-separated ids, installed or not."),),
        call=lambda repo, a, agent: _api().companions_verify(
            repo, a.get("id", "") or "", agent=agent
        ),
        payload=("verified", "skipped"),
    ),
    Command(
        path=(),
        tool="ddflow_companions_add",
        description="WRITES the agent config: registers companion MCP servers that are ALREADY installed (exit 3 for one that is not). dry_run=true FIRST; show the operator the entry: which servers an agent launches is their decision.",
        params=(
            Param("id", help="Comma-separated ids; default: every installed one."),
            Param("agents", help="Comma-separated agent keys (default: claude)."),
            Param(
                "dry_run",
                type="boolean",
                help="Report the exact entry that would be written; write nothing.",
            ),
        ),
        call=lambda repo, a, agent: _api().companions_add(
            repo,
            _api().Registration(
                ids=a.get("id", "") or "",
                agents=a.get("agents", "") or "",
                force=bool(a.get("force")),
                dry_run=bool(a.get("dry_run")),
            ),
            agent=agent,
        ),
        payload=("actions", "applied", "written", "refused"),
    ),
    # The command lines that stay hand-written, each with the tool that covers it.
    Command(path=("hooks", "status"), reason=_COVERED.format("ddflow_hooks")),
    Command(path=("hooks", "install"), reason=_COVERED.format("ddflow_hooks")),
    Command(path=("hooks", "uninstall"), reason=_COVERED.format("ddflow_hooks")),
    Command(path=("prompts", "list"), reason=_COVERED.format("ddflow_prompts")),
    Command(path=("prompts", "show"), reason=_COVERED.format("ddflow_prompts")),
    Command(path=("prompts", "eject"), reason=_COVERED.format("ddflow_prompts")),
    Command(path=("prompts", "get"), reason=_COVERED.format("ddflow_prompts")),
    Command(path=("companions", "list"), reason="covered by ddflow_companions"),
    Command(path=("companions", "add"), reason="covered by ddflow_companions_add"),
    Command(
        path=("hooks", "session-start"),
        reason="invoked BY the Claude Code SessionStart hook to put the brief into a new session; over MCP that is ddflow_brief",
    ),
    Command(
        path=("hooks", "pre-compact"),
        reason="invoked by Claude Code's own PreCompact hook with its JSON on stdin; an agent never calls it, and the record it writes is a session note (ddflow_session_note)",
    ),
    Command(
        path=("hooks", "prompt"),
        reason="invoked BY the harness's prompt hook with the prompt's JSON on stdin; an agent records its own words with ddflow_session_prompt",
    ),
    Command(
        path=("hooks", "run"),
        reason="the one entry point every agent's lifecycle hook calls, with that agent's JSON on stdin; an agent never calls it, and over MCP the same work is ddflow_brief, ddflow_session_prompt and ddflow_session_note",
    ),
    Command(
        path=("hooks", "check-msg"),
        reason="invoked BY the installed commit-msg hook with the message being committed; it is not something an agent calls",
    ),
    Command(
        path=("hooks", "check-commit"),
        reason="invoked BY the installed git hook, inside the commit that is being checked; it is not something an agent calls",
    ),
)

BY_TOOL = by_tool(COMMANDS)
