"""MCP tools: the gate pipeline and its checks: workflow, verify, ci, help.

One slice of the `TOOLS` registry, assembled in `surfaces/tools/__init__.py`."""

from __future__ import annotations

from typing import Any

from ..declared import review as RV
from ._common import _all_tools, _api

TOOLS: dict[str, dict[str, Any]] = {
    "ddflow_workflow": {
        "description": (
            "The rules THIS project runs by, in one answer: the gates every task and phase passes in order, the completion rules, parallelism caps, reviewers, and where each value came from. Call it before your first `ddflow_claim` and after any workflow change: instructions are computed once at start. Exit 1: the workflow does not hang together (e.g. a pipeline names a gate with no definition). Read-only."
        ),
        "properties": {},
        "api": lambda repo, a, agent: _api().workflow_show(repo),
        # The whole `data`: `ddflow workflow --json` has always emitted one object
        # with every section in it, and callers read `coherent` and `findings`.
        "payload": "",
    },
    "ddflow_workflow_pipeline": {
        "description": (
            "Set the ordered list of gates a task or a phase must pass. WRITES this project's config. Validated first: a gate id with no definition is REFUSED (it would block every item that reaches it); define it with `ddflow_workflow_gate`. Ask the operator before changing a pipeline -- it governs every future item, and removing a gate removes a check somebody added on purpose. dry_run shows what it would do."
        ),
        "properties": {
            "which": ("string", "'task' or 'phase'.", True),
            "gates": ("string", "Comma-separated gate ids, in the order they run.", True),
            "dry_run": ("boolean", "Report the change and write nothing.", False),
        },
        "api": lambda repo, a, agent: _api().workflow_pipeline(
            repo, a["which"], a["gates"], dry_run=bool(a.get("dry_run")), agent=agent
        ),
        "payload": ("key", "gates", "applied"),
    },
    "ddflow_workflow_gate": {
        "description": (
            "Define or change one gate, optionally in a pipeline. WRITES the project config. `command` makes a COMMAND gate (ddflow runs it; its exit code is the evidence); `prompt` makes an AGENT gate (you perform and record it); one is required. `into` adds it to a pipeline (`after` places it, default last); `required` blocks completion without it. Ask the operator first; prefer dry_run."
        ),
        "properties": {
            "id": ("string", "The gate id, e.g. 'lint' or 'security_scan'.", True),
            "command": ("string", "Shell command to run. Makes it a command gate.", False),
            "prompt": ("string", "What an agent must do. Makes it an agent gate.", False),
            "title": ("string", "Human-readable name.", False),
            "cwd": ("string", "'worktree' (default) or 'repo'.", False),
            "reviewer": (
                "string",
                "'different_family' to require a reviewer from another model family, "
                "or 'same_family_ok'.",
                False,
            ),
            "timeout": ("integer", "Seconds before the command counts as unavailable.", False),
            "applies_to": ("string", "'task', 'phase' or 'both'.", False),
            "into": ("string", "Add to the 'task', 'phase' or 'both' pipeline(s).", False),
            "after": ("string", "Place it after this gate. Default: last.", False),
            "required": ("boolean", "An item cannot complete without it.", False),
            "dry_run": ("boolean", "Report the change and write nothing.", False),
        },
        "api": lambda repo, a, agent: _api().workflow_gate(
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
        "payload": ("gate", "changed", "applied"),
    },
    "ddflow_workflow_drop": {
        "description": (
            "Take a gate out of every pipeline -- task, phase and promotion -- and out of `required`, so it does not become a requirement that requires nothing. WRITES config. The gate's DEFINITION stays, so putting it back is one call. Exit 2: it was in no pipeline. Ask the operator first: a gate in a pipeline is a check somebody added on purpose."
        ),
        "properties": {
            "id": ("string", "The gate id to remove from the pipelines.", True),
            "dry_run": ("boolean", "Report the change and write nothing.", False),
        },
        "api": lambda repo, a, agent: _api().workflow_drop(
            repo, a["id"], dry_run=bool(a.get("dry_run")), agent=agent
        ),
        "payload": ("gate", "removed_from", "applied"),
    },
    "ddflow_workflow_state": {
        "description": (
            "One-call project overview: workflow, rules, decisions, active work, queue and bugs with names and priorities, blockers, and a Mermaid gate diagram. Read-only."
        ),
        "properties": {},
        "api": lambda repo, a, agent: _api().workflow_overview(repo, agent),
        "payload": (
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
    },
    "ddflow_verify": RV.BY_TOOL["ddflow_verify"].tool_entry(),
    "ddflow_ci": {
        "description": "CI parity: run the pre-push checks on the branch merged with the base (run) or show what would run (status).",
        "properties": {
            "action": ("string", "run | status (default).", False),
            "ref": ("string", "Commit to check (run).", False),
            "base": ("string", "Branch merged in first (run).", False),
            "command": ("string", "Override [ci].command (run).", False),
        },
        "api": lambda repo, a, agent: _api().ci_tool(
            repo,
            action=a.get("action", "status") or "status",
            ref=a.get("ref", "HEAD") or "HEAD",
            base=a.get("base", "") or "",
            command=a.get("command", "") or "",
            agent=agent,
        ),
        "payload": "",
    },
    "ddflow_help": {
        "description": (
            "What ddflow IS, what it can do, and what the workflow is. Call this first if you have not used it before: the other descriptions explain one tool each and the connection instructions describe THIS repository; neither answers 'how am I meant to work here'. No argument: the loop from picking work to landing it, the exit codes, and every capability grouped by purpose. `topic`: workflow, import, gates, parallel, memory, recovery, config. Read-only; the pages are templates a project may override."
        ),
        "properties": {
            "topic": (
                "string",
                "workflow | import | gates | parallel | memory | recovery | config. "
                "Omit for the overview, which lists them.",
                False,
            ),
        },
        "api": lambda repo, a, agent: _api().help_topic(
            repo, topic=a.get("topic", "") or "", tools=_all_tools(), agent=agent
        ),
        "payload": ("topic", "text", "topics"),
    },
    "ddflow_import_verify": {
        "description": (
            "Was this project's history imported, is that still true, and did anyone FINISH it? Read-only. "
            "STATUS: what carries import provenance. STILL TRUE: whether the sources moved on (what a "
            "re-run would add) or an imported item names a missing file. FINISHED: imported tasks with "
            "no globs (the conflict detector cannot protect them) and phases claiming shipped work "
            "while a task under them is open. Call after any import. Exit 1 = findings; exit 2 = "
            "nothing ever imported. `ddflow_doctor` covers the rest."
        ),
        "properties": {},
        "api": lambda repo, a, agent: _api().import_verify(repo, agent=agent),
        "payload": "",
    },
}
