"""MCP tools: prompts, hooks, doctor, cadence, export, pins, precommit, tests.

One slice of the `TOOLS` registry, assembled in `surfaces/tools/__init__.py`."""

from __future__ import annotations

from typing import Any

from ..declared import records as R
from ..declared import setup as ST
from ._common import _api

TOOLS: dict[str, dict[str, Any]] = {
    "ddflow_prompts": {
        "description": (
            "Inspect the prompt templates this project uses and where each comes from "
            "(shipped, project override, or config path). `get` renders a workflow command "
            "or macro as prompts/get does; `eject` copies the shipped ones into "
            ".ddflow/prompts/ to edit as plain text."
        ),
        "properties": {
            "action": ("string", "list (default), show, get, or eject.", False),
            "name": ("string", "Template name.", False),
            "arg": ("array", "get: KEY=VALUE.", False),
        },
        "api": lambda repo, a, agent: _api().prompts(
            repo,
            action=a.get("action", "list") or "list",
            name=a.get("name", "") or "",
            agent=agent,
            # One KEY=VALUE sent as a bare string is one argument, not one per character.
            arg=[a["arg"]] if isinstance(a.get("arg"), str) else list(a.get("arg") or []),
        ),
        # Prose for SOME arguments, like `render`: `show`/`get` return the prompt TEXT and
        # `eject` the files it wrote, while `list` is a table callers parse.
        "payload": lambda a: "text" if a.get("action") in ("show", "get", "eject") else "rows",
        "text": lambda a: a.get("action") in ("show", "get", "eject"),
        "kind": "prompts",
    },
    "ddflow_hooks": {
        "description": (
            "Inspect or install the enforcement git hook — the one layer of this "
            "workflow that does not depend on the agent agreeing. It refuses a commit "
            "touching paths no live lease of yours covers. `status` reports whether it "
            "is installed AND whether the policy actually blocks, since a block policy "
            "with no hook installed enforces nothing."
        ),
        "properties": {
            "action": ("string", "status (default), install, uninstall.", False),
            "claude": (
                "boolean",
                "Install/uninstall the Claude Code SessionStart hook in .claude/settings.json instead of the git hook: every session, even after compaction, starts with the ddflow brief. Other hooks there are untouched.",
                False,
            ),
        },
        "api": lambda repo, a, agent: _api().hooks(
            repo,
            action=a.get("action", "status") or "status",
            claude=bool(a.get("claude")),
            agent=agent,
        ),
        # The FACTS. `message` is the prose rendering of them and stays OUT of the JSON
        # body. `session_hook` was ADDED deliberately with the SessionStart hook -- a
        # wire change of its own, not part of the migration this comment once guarded
        # -- and is `null` when the settings file could not be read.
        "payload": ("installed", "policy", "session_hook", "trailer_hook"),
    },
    "ddflow_upgrade": ST.BY_TOOL["ddflow_upgrade"].tool_entry(),
    "ddflow_doctor": ST.BY_TOOL["ddflow_doctor"].tool_entry(),
    "ddflow_cadence": {
        "description": (
            "Which periodic whole-repo passes are due — integration tests, "
            "architecture review, mutation testing, dedupe sweep, lessons "
            "compression. Derived from completed work, so there is no state "
            "file to drift."
        ),
        "properties": {
            "ran": ("string", "Record that this cadence just ran.", False),
            "note": ("string", "What the pass did, recorded with it.", False),
        },
        "api": lambda repo, a, agent: _api().cadence(
            repo, ran=a.get("ran", "") or "", note=a.get("note", "") or "", agent=agent
        ),
        "payload": "due",
    },
    "ddflow_export": {
        "description": (
            "Documents from the log (roadmap, bugs, status, worklog, sessions, decisions, rules, "
            "changelog). No doc: list. doc: capped markdown (`truncated`). Writes only with "
            "write=true AND a repo path. action: list|enable|disable|validate (enable names you "
            "and how to stop it)."
        ),
        "properties": {
            "action": ("string", "list|enable|disable|validate.", False),
            "mode": ("string", "enable: whole|region|append.", False),
            "doc": ("string", "Kind; omit to list.", False),
            "all": ("boolean", "The selected documents.", False),
            "since": ("string", "From YYYY-MM-DD.", False),
            "version": ("string", "One release.", False),
            "phase": ("string", "One phase.", False),
            "item": ("string", "Bugs item.", False),
            "status": ("string", "e.g. open.", False),
            "limit": ("integer", "At most N.", False),
            "tag": ("string", "One tag.", False),
            "session": ("string", "One session.", False),
            "max_bytes": ("integer", "Max 60000.", False),
            "diff": ("boolean", "Preview a write.", False),
            "check": ("boolean", "Is it fresh.", False),
            "write": ("boolean", "Write to path.", False),
            "path": ("string", "Repo path.", False),
        },
        "api": lambda repo, a, agent: _api().export_tool(repo, a, agent),
        "payload": "",
    },
    "ddflow_pins": {
        "description": (
            "BEFORE compressing or rewording an instruction file (a rulebook, a driver, AGENTS.md, CLAUDE.md, a prompt template): which of its text a test pins, which suites to re-run afterwards, and the longest stretches no test holds. A sentence that reads like rationale is often a rule a test asserts. Exit 2: no Python suite to read pins from -- then treat ALL of it as pinned. Free text is a lower bound, not permission."
        ),
        "properties": {
            "document": ("string", "Path of the instruction file, relative to the repo.", True),
            "tests": ("string", "Comma-separated test dirs (default: tests,test).", False),
            "min_needle": (
                "integer",
                "Shortest string literal that counts as a pin (default 12).",
                False,
            ),
            "top": ("integer", "How many free stretches to return (default 10).", False),
        },
        "api": lambda repo, a, agent: _api().pins(
            repo,
            a["document"],
            tests=tuple(t.strip() for t in (a.get("tests") or "").split(",") if t.strip()),
            min_chars=None if a.get("min_needle") is None else int(a["min_needle"]),
            top=int(a.get("top") if a.get("top") is not None else 10),
        ),
        "payload": "",
    },
    "ddflow_precommit": {
        "description": (
            "A .pre-commit-config.yaml proposed for THIS repository: its stacks (Python, shell, Docker, JS, Go, Rust; YAML/TOML/JSON checks) mapped to pinned hooks, plus ddflow's check-commit and check-msg as local hooks, so pre-commit owns .git/hooks/ (remove ddflow's own first; the body names them). Proposes; installs nothing. `write` creates the file, REFUSED (exit 3) when one exists. The body names missing programs. Installing pre-commit is the operator's call."
        ),
        "properties": {
            "ddflow_cmd": (
                "string",
                "How the local hooks reach ddflow (default `ddflow` on PATH).",
                False,
            ),
            "write": ("boolean", "Create the file; never replaces an existing one.", False),
        },
        "api": lambda repo, a, agent, called_from=None: _api().precommit(
            repo,
            where=called_from,
            # Absent means the default; an empty string is passed on, and refused, as on
            # the CLI.
            ddflow_cmd="ddflow" if a.get("ddflow_cmd") is None else a["ddflow_cmd"],
            write=bool(a.get("write")),
            agent=agent,
        ),
        "payload": "",
        # The proposal is for the checkout the caller stands in, not the primary.
        "wants_called_from": True,
    },
    "ddflow_tests": {
        "description": (
            "AFTER EACH CHANGE: the tests your change reaches (changed tests, tests importing a changed module directly or one step removed, tests named after it, a changed conftest's or data file's), each with why, and a command running them IN PARALLEL. Run it; do not reason about which matter. Never a pass: the WHOLE suite still runs before merge. `item`: diff that item's worktree. Exit 2: no test reaches the change."
        ),
        "properties": {
            "item": ("string", "The item whose worktree and base to use.", False),
            "base": ("string", "Compare against this ref instead of the item's base.", False),
        },
        "api": lambda repo, a, agent, called_from=None: _api().relevant_tests(
            repo,
            item=a.get("item", "") or "",
            where=called_from,
            base=a.get("base", "") or "",
            agent=agent,
        ),
        "payload": "",
        # Without `item`, the diff is the caller's own checkout -- the worktree it is
        # standing in, not the primary the server was started on.
        "wants_called_from": True,
    },
    "ddflow_session_prompt": R.BY_TOOL["ddflow_session_prompt"].tool_entry(),
}
