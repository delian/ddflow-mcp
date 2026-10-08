"""MCP tools: reading the queue and finding things: recover, board, recall, status, identity.

One slice of the `TOOLS` registry, assembled in `surfaces/tools/__init__.py`."""

from __future__ import annotations

from typing import Any

from ..declared import knowledge as K
from ._common import _api

TOOLS: dict[str, dict[str, Any]] = {
    "ddflow_recover": {
        "description": (
            "Find work left behind by a crashed agent: expired leases, orphaned "
            "worktrees, items stuck running. Reports what each worktree contains and "
            "never deletes anything. Run this at the start of any session that follows "
            "an interruption."
        ),
        "properties": {
            "item": ("string", "Restrict to one item.", False),
            "apply": (
                "boolean",
                "Act on the advice: release the leases and remove the worktrees reported as holding nothing. Only a tree MEASURED as having no uncommitted and no unmerged work is touched; 'could not tell' is not 'empty'.",
                False,
            ),
        },
        "api": lambda repo, a, agent: _api().recover(
            repo, item=a.get("item", "") or "", apply=bool(a.get("apply")), agent=agent
        ),
        "payload": "found",
    },
    "ddflow_board": {
        "description": "The whole work queue as a readable board, with the critical path.",
        "properties": {"phase": ("string", "Restrict to one phase.", False)},
        "api": lambda repo, a, agent: _api().board(
            repo, phase=a.get("phase", "") or "", agent=agent
        ),
        "payload": "text",
        "text": True,
        "kind": "board",
    },
    "ddflow_progress": {
        "description": (
            "What work has ACTUALLY been done, aggregated from the event log: attempts "
            "per item, wall-clock held, gate runs, commits produced, and who did them. "
            "Use it to answer 'how much effort has gone into this' and to see an item's "
            "full gate history including the outcomes that were not passes."
        ),
        "properties": {
            "id": ("string", "One item, with its per-attempt detail.", False),
            "limit": ("integer", "Rows returned, most effort first (default 25; 0 = all).", False),
        },
        "api": lambda repo, a, agent: _api().progress(repo, a.get("id", "") or ""),
        # The pre-migration body was the ROW ARRAY. Preserved exactly.
        "payload": "rows",
    },
    "ddflow_loops": {
        "description": (
            "Detect circular references and runtime loops: dependency cycles, an item "
            "claimed and given up over and over, a gate whose verdict keeps flipping, "
            "a gate failing again with identical output (repeated_failure), "
            "work completed and reopened repeatedly, duplicate items writing the same "
            "files, and a queue where events keep arriving but nothing advances. "
            "CALL THIS WHEN WORK FEELS REPETITIVE: stop and re-plan rather than retry "
            "the same thing. [] when nothing is wrong."
        ),
        "properties": {},
        "api": lambda repo, a, agent: _api().loops(repo),
        # The pre-migration body was the findings ARRAY. Preserved exactly.
        "payload": "findings",
    },
    "ddflow_cleanup": {
        "description": (
            "Classify every ddflow worktree and branch: merged (safe to remove), unmerged (carries commits nobody landed), dirty (uncommitted edits: a human looks), orphan, or stale branch. Reports by default; apply=true removes merged worktrees and branches and lands commits for items the queue considers done. A dirty tree is NEVER touched automatically: it exists nowhere else."
        ),
        "properties": {"apply": ("boolean", "Perform the safe actions.", False)},
        "api": lambda repo, a, agent: _api().cleanup(repo, apply=bool(a.get("apply")), agent=agent),
        "payload": ("trees", "stale_branches"),
    },
    "ddflow_onboard": {
        "description": (
            "The onboarding stages in one call: status (standing drift report), preflight, legacy, memory, test-gate, verify. Propose by default; apply=true acts on the safe/approved items and accepts names."
        ),
        "properties": {
            "stage": (
                "string",
                "A stage name: status (the drift report, default), preflight, legacy, memory, test-gate or verify.",
                False,
            ),
            "apply": ("boolean", "Act on the proposal (preflight/legacy/memory).", False),
            "accept": (
                "array",
                "Names to approve (preflight leftovers, memory facts); empty = everything the report marked safe.",
                False,
            ),
        },
        "api": lambda repo, a, agent: _api().onboard_run(
            repo,
            stage=str(a.get("stage") or "status"),
            apply=bool(a.get("apply")),
            accept=tuple(a.get("accept") or ()),
            agent=agent,
        ),
        "payload": "",
    },
    "ddflow_recall": K.BY_TOOL["ddflow_recall"].tool_entry(),
    "ddflow_similar": K.BY_TOOL["ddflow_similar"].tool_entry(),
    "ddflow_dupes": K.BY_TOOL["ddflow_dupes"].tool_entry(),
    "ddflow_link": K.BY_TOOL["ddflow_link"].tool_entry(),
    "ddflow_status": {
        "description": (
            "The state of the whole project in one answer: how many tasks are done and "
            "which, what is in flight and who holds it, what is ready to start, what is "
            "blocked, how many agent-hours and commits went in, and whether anything is "
            "looping or waiting to be recovered. This is the tool for 'what is the "
            "status of this project?' and 'what has been completed?'."
        ),
        "properties": {},
        "api": lambda repo, a, agent: _api().status(repo, agent=agent),
        # The whole object. `_render` is stripped by `Outcome.body`.
        "payload": "",
    },
    "ddflow_identify": {
        "description": (
            "Declare WHO you are on this connection before anything that writes. Call it first when 2+ agents or subagents work this repository at once: identity attributes every claim, gate outcome and review, and the tree-derived default merges several agents in one tree into one identity with no error (a review would pass independence against itself). Pick a short stable name (your role), distinct from the others'. Idempotent. A SUBAGENT sharing its parent's connection must NOT call this; it passes `as_agent` on each call instead (the CLI's `--agent`). Stateless 2026-07-28 requests: persists nothing; name yourself per call (as_agent or _meta ddflow/agent)."
        ),
        "properties": {
            "agent": (
                "string",
                "A short stable name, e.g. 'reviewer-2' (letters, digits, . _ -; max 64; it names your log file). OMIT to reset to the tree-derived default.",
                False,
            ),
        },
        "identify": True,
    },
}
