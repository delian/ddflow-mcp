"""An MCP stdio server for ddflow — implemented in the standard library alone.

Why hand-rolled rather than `pip install mcp`: portability is the entire point of this
project. A workflow kernel that only installs where a package index is reachable is not
portable, and the MCP stdio transport is newline-delimited JSON-RPC 2.0 — a few hundred
lines, fully specified, and stable. Taking a dependency to avoid writing them would
trade the property we are optimising for against a convenience we do not need.

The tool surface is deliberately **the same functions the CLI calls**. MCP is a second
door onto one implementation, never a second implementation. That is what keeps an
agent driving ddflow over MCP and an agent driving it over a shell from diverging —
and it is why an agent with neither (a human, a CI job, a `Makefile`) loses nothing.

Notes on protocol handling:

* The client's ``protocolVersion`` is echoed back when we recognise it, else we answer
  with our newest. Refusing an unknown version outright breaks on every client that
  ships ahead of us, which for a tool meant to work with five different agents is the
  likelier direction of drift.
* Every tool returns text content. Structured results are JSON *inside* that text,
  because ``structuredContent`` support is uneven across clients and a result an agent
  cannot read is worse than a verbose one it can.
* Errors are returned as ``isError: true`` results rather than JSON-RPC errors, which
  is what lets the model see the failure and correct, instead of the transport
  swallowing it.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any

# Absolute on purpose: `ddflow/__init__.py` is the one declaration of the version and has no
# dependencies, so this cannot cycle; a relative `from .. import` would read as a layer.
# A plain import, never importlib.metadata: installed metadata is stale in a source tree.
from ddflow import __version__ as _VERSION
from ddflow.core.events import SkewRefused

SUPPORTED_PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
SERVER_INFO = {"name": "ddflow", "version": _VERSION, "title": "ddflow work-queue kernel"}


#: Tool surface. Each entry maps an MCP tool onto an argv the CLI already understands,
#: so there is exactly one implementation of every operation.
#: (description, {property: (json_type, description, required)}, argv builder)
def _AGENT_KEYS() -> list[str]:
    """Every supported harness, from the one registry that defines them.

    The import is inside the function only to keep it out of this module's header; it is
    NOT deferred in any meaningful sense, because `TOOLS` calls this while it is being
    built, so `services.adopt` is imported when this module is. An earlier version of this
    docstring claimed the opposite — roborev checked `sys.modules` and disproved it. The
    accurate statement is that the list has ONE source, and a hand-kept copy in the tool
    description has drifted twice.
    """
    from ..services.adopt import AGENT_TARGETS

    return list(AGENT_TARGETS)


#: `ddflow_wait` over MCP: shorter than the CLI's default, because the client -- not
#: ddflow -- decides when a tool call has hung, and a timed-out call is a lost answer.
#: Capped for the same reason; an agent that wants longer calls again.
MCP_WAIT_DEFAULT_S = 300
MCP_WAIT_MAX_S = 1800


def _wait_timeout(a: dict[str, Any]) -> float:
    """`timeout` as given (0 included -- it means "ask, do not sleep"), else the MCP
    default; never above the cap."""
    t = a.get("timeout")
    return min(float(MCP_WAIT_DEFAULT_S if t is None else t), float(MCP_WAIT_MAX_S))


TOOLS: dict[str, dict[str, Any]] = {
    "ddflow_brief": {
        "description": (
            "START HERE every session. Returns a budgeted pack: work recoverable after "
            "a crash, the current item, what is ready to start now, why everything else "
            "is blocked, and the past lessons ranked as relevant to this task. Use this "
            "INSTEAD of reading the project's lesson or rule files — it is the same "
            "information retrieved for the task at hand, at a fraction of the tokens."
        ),
        "properties": {
            "item": ("string", "Focus on this phase or task id (optional).", False),
            "phase": ("string", "Restrict the ready set to this phase (optional).", False),
            "check_recovery": (
                "boolean",
                "Also scan for crashed agents' worktrees and lead with them: unclaimed work left by a dead process is the one thing to know BEFORE picking up something new.",
                False,
            ),
        },
        "api": lambda repo, a, agent: _api().brief(
            repo,
            item=a.get("item", "") or "",
            phase=a.get("phase", "") or "",
            check_recovery=bool(a.get("check_recovery", _api().DEFAULT_CHECK_RECOVERY)),
            agent=agent,
        ),
        # PROSE: the budgeted reading pack is text to read.
        "payload": "text",
        "text": True,
        "kind": "brief",
    },
    "ddflow_next": {
        "description": (
            "What may be started RIGHT NOW, and for everything that may not, the reason. "
            "Independent items in the ready set can be run in parallel worktrees by "
            "separate agents. Returns ready=[] when nothing is actionable — that is a "
            "result, not an error, and it never means 'pick something anyway'."
        ),
        "properties": {
            "phase": (
                "string",
                "Restrict to one phase (the 'implement phase X' entry point).",
                False,
            ),
            "kind": ("string", "'task' (default) or 'phase'.", False),
        },
        "api": lambda repo, a, agent: _api().next_item(
            repo,
            kind=a.get("kind") or _api().DEFAULT_NEXT_KIND,
            phase=a.get("phase", "") or "",
            agent=agent,
        ),
        "payload": "",
    },
    "ddflow_claim": {
        "description": (
            "Lease an item and create its isolated git worktree. Refuses (exit 3) if "
            "another agent holds it or holds an item whose file globs overlap, and names "
            "what you could take instead. NEVER steals an expired lease: a crashed "
            "agent's worktree often holds finished work."
        ),
        "properties": {
            "id": ("string", "Item id to claim.", True),
            "globs": ("string", "Comma-separated path globs this work will write.", False),
            "note": ("string", "What you intend to do.", False),
            "no_worktree": (
                "boolean",
                "Lease the item without creating a worktree. For work that is not a "
                "code change — a research or review task.",
                False,
            ),
            "resources": (
                "string",
                "Physical resources this claim holds, e.g. 'gpu:2'; they REPLACE the item's declared ones. Refused (exit 3) when live claims already use the capacity ([schedule] resources), every holder counted.",
                False,
            ),
            "force": (
                "boolean",
                "Override a refusal. Legitimate only to retry after `ddflow_recover` said a crashed agent's worktree holds nothing. Forcing past a dependency or live lease is how two agents write one file; recorded either way.",
                False,
            ),
        },
        "api": lambda repo, a, agent, called_from=None: _api().claim(
            repo,
            a["id"],
            globs=a.get("globs", "") or "",
            note=a.get("note", "") or "",
            force=bool(a.get("force")),
            no_worktree=bool(a.get("no_worktree")),
            called_from=called_from,
            resources=a.get("resources", "") or "",
            agent=agent,
        ),
        # `rebound`/`here`: the item kept its OWN tree from an earlier claim, and whether
        # the caller is standing in it -- over MCP the payload is the only way an agent
        # learns it has to move there.
        "payload": (
            "item",
            "holder",
            "worktree",
            "branch",
            "base",
            "rebound",
            "here",
            "port",
            "port_advice",
        ),
        # `claim` is the one operation that needs to know WHERE THE CALLER IS, not just
        # which repo: adoption turns on whether the caller was already standing in a
        # worktree. The dispatcher passes it only to tools that ask.
        "wants_called_from": True,
    },
    "ddflow_heartbeat": {
        "description": (
            "Renew the lease on an item. Call periodically during long work, "
            "or the lease expires and another agent may take the item."
        ),
        "properties": {"id": ("string", "Item id.", True)},
        "api": lambda repo, a, agent, called_from=None: _api().heartbeat(
            repo, a["id"], agent=agent, called_from=called_from
        ),
        "payload": ("renewed", "waiters", "globs_withheld"),
        # The item's own tree renews its lease whoever claimed it -- identity is derived
        # from the tree, so without WHERE the caller is this said "no lease held" from
        # exactly the tree `claim` made.
        "wants_called_from": True,
    },
    "ddflow_gate_status": {
        "description": (
            "Where an item stands in its quality pipeline, which gate is next, and the "
            "instruction for that gate. Gates marked '?' did not run — that is a coverage "
            "gap, never a pass."
        ),
        "properties": {"id": ("string", "Item id.", True)},
        "api": lambda repo, a, agent: _api().gate_status(repo, a["id"], agent=agent),
        # PROSE: the body carries the next gate's INSTRUCTION, which is the half an
        # agent acts on. `--json` gives the structured pipeline instead.
        "payload": "text",
        "text": True,
        "kind": "gate.status",
    },
    "ddflow_gate_run": {
        "description": (
            "Execute a command gate (tests, linters) and record the result "
            "with its evidence. Agent gates cannot be run this way; they are "
            "recorded with ddflow_gate_record."
        ),
        "properties": {
            "id": ("string", "Item id.", True),
            "gate": ("string", "Gate id, e.g. unit_tests.", True),
        },
        "api": lambda repo, a, agent, called_from=None: _api().gate_run(
            repo, a["id"], a["gate"], agent=agent, called_from=called_from
        ),
        "wants_called_from": True,
        "payload": ("gate", "outcome", "evidence"),
    },
    "ddflow_gate_record": {
        "description": (
            "Record the outcome of a gate you performed (research, a review, a bug hunt). "
            "outcome is one of passed/failed/unavailable/partial/skipped. "
            "If a reviewer or tool could not run, record 'unavailable' with a reason, "
            "never 'passed'. Pass the reviewer's model for the family check."
        ),
        "properties": {
            "id": ("string", "Item id.", True),
            "gate": ("string", "Gate id.", True),
            "outcome": ("string", "passed | failed | unavailable | partial | skipped", True),
            "reason": ("string", "Required for failed/unavailable/partial/skipped.", False),
            "evidence": ("string", "What you ran and what it said. Required by some gates.", False),
            "model": ("string", "REVIEWER's model, e.g. 'gemini-2.5-pro'.", False),
            "reviewer_model": ("string", "Like `model`; says it IS the reviewer.", False),
            "reviewed_sha": (
                "string",
                "Commit reviewed (roborev review <sha>); must be the branch.",
                False,
            ),
            "command": (
                "string",
                "The command you ran. With `exit_code` it makes an outcome evidence; "
                "a gate in `gates.evidence_required` is rejected without them.",
                False,
            ),
            "exit_code": ("string", "That command's exit code.", False),
            "output_file": (
                "string",
                "Path to its full output; a digest is recorded.",
                False,
            ),
        },
        "api": lambda repo, a, agent, called_from=None: _api().gate_record(
            repo,
            a["id"],
            a["gate"],
            outcome=a.get("outcome", "passed") or "passed",
            reason=a.get("reason", "") or "",
            evidence=_api().GateEvidence(
                note=a.get("evidence", "") or "",
                command=a.get("command", "") or "",
                exit_code=a.get("exit_code"),
                model=a.get("reviewer_model", "") or a.get("model", "") or "",
                model_is_reviewer=bool(a.get("reviewer_model")),
                reviewed_sha=a.get("reviewed_sha", "") or "",
                output_file=a.get("output_file", "") or "",
            ),
            agent=agent,
            called_from=called_from,
        ),
        "wants_called_from": True,
        "payload": ("gate", "outcome", "warning"),
        # B160: `warning` carries the out-of-order NOTE. It printed to stderr only, and
        # `_run_cli` captured stdout — so an agent recording `rubber_duck` before
        # `implement` was told NOTHING, and the only trace was a `gate.out_of_order`
        # event nobody reads back. Third occurrence of the class whose comment still
        # sits in `cmd_complete`: "it used to print only in human mode, so an agent
        # driving over MCP was never told that a gate had not run."
        #
        # This ADDS a key to a body consumers already parse, which is why it is its own
        # change rather than part of the migration: a migration removes a duplicated
        # rendering, it does not redefine contracts. `null` when the recording was in
        # order, so the shape is stable and a consumer can branch on it.
    },
    "ddflow_gate_verify": {
        "description": (
            "Break what a gate guards and require it to NOTICE: applies each mutation "
            "registered on the gate, runs it, requires a non-zero exit, restores the "
            "file. A gate that cannot fail reports success on every change. Exit 1: "
            "the gate did NOT catch its mutation, or none is registered. Exit 3 on a "
            "HUMAN-approval gate (nothing to mutate). A mutation whose `old` text is "
            "absent or ambiguous is a FAILURE, not a skip: the gate ran on pristine "
            "source."
        ),
        "properties": {
            "id": ("string", "Item whose worktree to mutate in.", True),
            "gate": ("string", "Gate id. Must be a command gate.", True),
        },
        "api": lambda repo, a, agent: _api().gate_verify(repo, a["id"], a["gate"], agent=agent),
        "payload": ("gate", "reason", "results", "verified"),
    },
    "ddflow_gate_skip": {
        "description": (
            "Skip a gate ON THE RECORD, with a mandatory reason: the auditable escape hatch. `gates.require_outcome` means a silent gate BLOCKS completion, so the alternative to a skip is forcing past everything at once; a skip names the single step dropped and why, permanently in the log. A gate in `gates.required` still blocks when skipped."
        ),
        "properties": {
            "id": ("string", "Item id.", True),
            "gate": ("string", "Gate id.", True),
            "reason": (
                "string",
                "Why this step does not apply HERE. 'n/a' is not a reason: the next "
                "person reads this to decide whether you were right.",
                True,
            ),
        },
        "api": lambda repo, a, agent: _api().gate_record(
            repo, a["id"], a["gate"], skip=True, reason=a.get("reason", "") or "", agent=agent
        ),
        "payload": ("gate", "outcome"),
    },
    "ddflow_complete": {
        "description": (
            "Finish an item. Refuses (exit 3) when a required gate has not passed, when a "
            "phase still has open tasks, or when no reviewer came from a different model "
            "family than the author. Pass your own model as 'model'."
        ),
        "properties": {
            "id": ("string", "Item id.", True),
            "sha": ("string", "Commit sha this shipped as.", False),
            "model": ("string", "The AUTHOR's model.", False),
            "changelog": (
                "string",
                "Optional 'Added|Changed|Deprecated|Removed|Fixed|Security: text', or skip.",
                False,
            ),
            "regression_test": (
                "string",
                "For a fix task: the test that now guards its bug(s); closes them.",
                False,
            ),
            "force": (
                "boolean",
                "Complete over unmet conditions; each is recorded as overridden, forever. "
                "Prefer `ddflow_gate_skip` with a reason: it drops one named step.",
                False,
            ),
        },
        "api": lambda repo, a, agent: _api().complete(
            repo,
            a["id"],
            sha=a.get("sha", "") or "",
            force=bool(a.get("force")),
            model=a.get("model", "") or "",
            changelog=a.get("changelog", "") or "",
            regression_test=a.get("regression_test", "") or "",
            agent=agent,
        ),
        "payload": (
            "id",
            "sha",
            "independence",
            "forced",
            "coverage_gaps",
            "note",
            "woke",
            "bugs_closed",
        ),
    },
    "ddflow_merge": {
        "description": (
            "Land an item's branch without ever switching a checkout's branch. With "
            "[flow].integration = 'pr' it pushes and opens (or updates) a pull request "
            "instead, releases your lease and parks the item in REVIEW — take the next "
            "item; `ddflow_pr_sync` completes it once a person merges it."
        ),
        "properties": {
            "id": ("string", "Item id.", True),
            "message": ("string", "Merge commit message.", False),
            "keep": ("boolean", "Keep the worktree after merging, for inspection.", False),
            "model": (
                "string",
                "The AUTHOR's model. In PR mode the item completes later, at "
                "`ddflow_pr_sync`, and the reviewer-independence check needs it then.",
                False,
            ),
            "allow_dirty": (
                "boolean",
                "Merge although the worktree has uncommitted changes; they are NOT included. Pass it only once you have looked at what is dirty and decided it is build output.",
                False,
            ),
            "allow_empty": (
                "boolean",
                "Land a branch with no commits ahead of its target. Refused by default: usually the item is bound to the wrong tree (rebind with ddflow_update worktree).",
                False,
            ),
            "branch": (
                "string",
                "For an item claimed with no_worktree: the branch to land (default: the branch checked out where this connection runs). Changes outside the item's globs come back as outside_globs.",
                False,
            ),
        },
        "api": lambda repo, a, agent, called_from=None: _api().merge_item(
            repo,
            a["id"],
            message=a.get("message", "") or "",
            allow_dirty=bool(a.get("allow_dirty")),
            allow_empty=bool(a.get("allow_empty")),
            keep=bool(a.get("keep")),
            model=a.get("model", "") or "",
            branch=a.get("branch", "") or "",
            called_from=called_from,
            agent=agent,
        ),
        "wants_called_from": True,
        "payload": (
            "id",
            "sha",
            "branch_head",
            "base",
            "pr",
            "branch",
            "outside_globs",
            "worktree",
            "worktree_removed",
        ),
    },
    "ddflow_pr_sync": {
        "description": (
            "Ask the forge (GitHub/GitLab) what reviewers did with every request in REVIEW and record it: a merged request completes its item (and retargets what is stacked on it), requested changes return the item to the queue WITH the review text, a closed one is parked for a person, an approved green one is merged when [flow].pr_merge = 'on_approval'. `ddflow_next` does this itself with [flow].sync_on_next. Exit 2: the forge could not be asked -- NOT 'nothing changed'."
        ),
        "properties": {"item": ("string", "Only this item (optional).", False)},
        "api": lambda repo, a, agent: _api().pr_sync(
            repo, item=a.get("item", "") or "", agent=agent
        ),
        "payload": "",
    },
    "ddflow_pr_status": {
        "description": (
            "Every item's pull request as last recorded — review, checks, target, rounds "
            "of changes and when it was last looked at. Reads the log only; "
            "`ddflow_pr_sync` asks the forge."
        ),
        "properties": {},
        "api": lambda repo, a, agent: _api().pr_status(repo, agent=agent),
        "payload": "",
    },
    "ddflow_pr_threads": {
        "description": (
            "An item's review threads, read live from the forge. With `thread`, `reply` on "
            "it and/or `resolve` it, so the reviewer sees what was addressed. Exit 2 = "
            "forge not reached; 3 = refused."
        ),
        "properties": {
            "id": ("string", "The item.", True),
            "thread": ("string", "Thread id, as listed.", False),
            "reply": ("string", "Reply text.", False),
            "resolve": ("boolean", "Resolve it.", False),
        },
        "api": lambda repo, a, agent: _api().pr_threads(
            repo,
            a["id"],
            thread=a.get("thread", "") or "",
            reply=a.get("reply", "") or "",
            resolve=bool(a.get("resolve")),
            agent=agent,
        ),
        "payload": "",
    },
    "ddflow_version_show": {
        "description": (
            "The current version (highest `<tag_prefix>X.Y.Z` tag reachable from the "
            "release branch), the next one, the bump and why (Conventional Commits plus "
            "the tags of finished items), and the release notes. Reads only."
        ),
        "properties": {
            "bump": ("string", "Force major | minor | patch instead of the computed bump.", False),
            "line": ("string", "A maintenance line (default: the current one).", False),
        },
        "api": lambda repo, a, agent: _api().version_show(
            repo, bump=a.get("bump", "") or "", line=a.get("line", "") or "", agent=agent
        ),
        "payload": "",
    },
    "ddflow_version_cut": {
        "description": (
            "Tag the next version. trunk: tags the base branch. gitflow: release/X from "
            "develop, merged to production, tagged, tag merged back; with pull requests, "
            "opens the release request (`ddflow_pr_sync` tags it once merged). Exit 2 = "
            "nothing to release."
        ),
        "properties": {
            "bump": ("string", "major | minor | patch.", False),
            "version": ("string", "MAJOR.MINOR.PATCH.", False),
            "push": ("boolean", "Publish the tag and branches.", False),
            "dry_run": ("boolean", "Report only.", False),
            "line": (
                "string",
                "A maintenance line: tagged where it stands; its major is kept.",
                False,
            ),
            "changelog": (
                "boolean",
                "Also write CHANGELOG.md.",
                False,
            ),
            "force": ("boolean", "Overwrite a hand-edited file.", False),
        },
        "api": lambda repo, a, agent: _api().version_cut(
            repo,
            bump=a.get("bump", "") or "",
            version=a.get("version", "") or "",
            push=bool(a.get("push")),
            dry_run=bool(a.get("dry_run")),
            line=a.get("line", "") or "",
            changelog=bool(a.get("changelog")),
            force=bool(a.get("force")),
            agent=agent,
        ),
        "payload": "",
    },
    "ddflow_promote_add": {
        "description": (
            "File a PROMOTION to an environment branch ([flow].environments): a task that "
            "merges the branch immediately upstream into it, runs the promotion pipeline "
            "and lands by merge or request -- always one step downstream. Exit 2 = nothing "
            "to promote; exit 3 = refused (unknown environment, one open, branch missing)."
        ),
        "properties": {
            "env": ("string", "The environment to promote TO.", True),
            "force": ("boolean", "File it even with nothing to carry.", False),
        },
        "api": lambda repo, a, agent: _api().promote_add(
            repo, a["env"], force=bool(a.get("force")), agent=agent
        ),
        "payload": "",
    },
    "ddflow_promote_deployed": {
        "description": (
            "Record the sha a deploy put LIVE in an environment (from the deploy hook); "
            "promote_status then shows what runs there. Exit 3 = refused."
        ),
        "properties": {
            "env": ("string", "Environment.", True),
            "sha": (
                "string",
                "Deployed commit (default: branch head).",
                False,
            ),
        },
        "api": lambda repo, a, agent: _api().promote_deployed(
            repo, a["env"], sha=a.get("sha", "") or "", agent=agent
        ),
        "payload": "",
    },
    "ddflow_promote_status": {
        "description": (
            "Each environment: head, commits behind its upstream, open promotion, "
            "auto_promote, and the live (deployed) sha. Reads only."
        ),
        "properties": {},
        "api": lambda repo, a, agent: _api().promote_status(repo, agent=agent),
        "payload": "",
    },
    "ddflow_flow_show": {
        "description": (
            "How THIS project works: its branching model, release lines, and every workflow "
            "choice (model, integration, pr_merge, port_strategy, ...) with its value, the "
            "options, and who decided -- the operator's config, a recorded choice, or a "
            "default nobody chose. `pending` lists relevant choices nobody has made: ask the "
            "operator, or pick what suits the project with `ddflow_flow_choose`."
        ),
        "properties": {},
        "api": lambda repo, a, agent: _api().flow_show(repo, agent=agent),
        "payload": "",
    },
    "ddflow_flow_choose": {
        "description": (
            "Record a workflow choice for this project, attributed to you, with a reason the "
            "next agent will read. Make it when the operator told you, or when they left it "
            "to you -- a choice left unmade is defaulted at first use and followed from then "
            "on. The operator's config file wins over a recorded choice; the result says "
            "`in_effect: false` when it does."
        ),
        "properties": {
            "knob": ("string", "The choice, e.g. port_strategy (see ddflow_flow_show).", True),
            "value": ("string", "One of its options.", True),
            "reason": ("string", "Why this suits the project.", False),
        },
        "api": lambda repo, a, agent: _api().flow_choose(
            repo, a["knob"], a["value"], reason=a.get("reason", "") or "", agent=agent
        ),
        "payload": "",
    },
    "ddflow_phase_add": {
        "description": (
            "Add a phase to the queue. A phase is a unit of REVIEW: it gets its own "
            "research, its own whole-phase test pass and live smoke run, and it merges "
            "as one coherent feature. Group tasks into a phase when they only make "
            "sense shipped together."
        ),
        "properties": {
            "id": ("string", "Short stable id, e.g. 'P2' or 'auth'.", True),
            "title": ("string", "One-line description.", False),
            "needs": ("string", "Comma-separated ids this phase depends on.", False),
            "globs": (
                "string",
                "Comma-separated path globs this phase writes: what lets two agents work different phases in parallel; the phase's dependencies are INHERITED by every task in it.",
                False,
            ),
            "body": ("string", "Detail, acceptance criteria, context.", False),
            "tags": ("string", "Comma-separated tags.", False),
            "priority": ("integer", "Lower is offered first (default 100).", False),
            "line": (
                "string",
                "Release line this lands on (a name from [flow.lines], or the current "
                "line). Omit for the current line; tasks inherit a phase's line.",
                False,
            ),
            "readd": (
                "boolean",
                "File a REMOVED item's id again with this definition. An id still in the "
                "queue is always refused -- change that item with ddflow_update.",
                False,
            ),
        },
        "api": lambda repo, a, agent: _api().phase_add(
            repo,
            a["id"],
            title=a.get("title", "") or "",
            needs=a.get("needs", "") or "",
            globs=a.get("globs", "") or "",
            body=a.get("body", "") or "",
            tags=a.get("tags", "") or "",
            priority=int(a.get("priority") or _api().DEFAULT_PRIORITY),
            line=a.get("line", "") or "",
            readd=bool(a.get("readd")),
            answer=_answer(a),
            agent=agent,
        ),
        "payload": ("id",),
    },
    "ddflow_split": {
        "description": (
            "Split an item into sub-tasks IN PLACE when the work turns out to be two things -- the moment you discover it; mid-task discovery is normal. The original keeps its id and history and becomes an umbrella that completes when its children do; closing it and opening two new ones would lose the thread between what was planned and what happened. Children inherit the parent's globs: give each its own afterwards if they write different files, or they cannot run in parallel."
        ),
        "properties": {
            "id": ("string", "The item to split.", True),
            "into": ("string", "Comma-separated 'sub-id=title' pairs. At least two.", True),
            "globs": ("string", "Globs for the children (default: inherit).", False),
            "needs": (
                "string",
                "Dependencies for the FIRST child. The others chain from it if you set "
                "theirs with ddflow_update.",
                False,
            ),
        },
        "api": lambda repo, a, agent: _api().split(
            repo,
            a["id"],
            # The MCP argument is ONE comma-separated string; the CLI takes repeated
            # `--into`. Split here rather than in the api, so the api keeps the shape
            # that cannot lose a spec containing a comma in its title.
            into=[x.strip() for x in str(a.get("into", "")).split(",") if x.strip()],
            globs=a.get("globs", "") or "",
            needs=a.get("needs", "") or "",
            agent=agent,
        ),
        "payload": ("item", "created"),
    },
    "ddflow_task_add": {
        "description": (
            "Add a task to a phase. ALWAYS set globs to the paths this task will write: "
            "they are what lets two agents work in parallel safely, and an unset glob "
            "means the conflict detector cannot protect you."
        ),
        "properties": {
            "id": ("string", "Short stable id, e.g. 'P2.T1'.", True),
            "phase": ("string", "Owning phase id. Give this OR `parent`.", False),
            "parent": (
                "string",
                "Owning phase id, OR another TASK's id, which makes this a SUB-TASK with its own globs and dependencies, run in parallel with its siblings. Same field as `phase` (the CLI has both names).",
                False,
            ),
            "title": ("string", "One-line description.", False),
            "needs": ("string", "Comma-separated ids this task depends on.", False),
            "globs": ("string", "Comma-separated path globs this task writes.", False),
            "body": ("string", "Detail and acceptance criteria.", False),
            "tags": ("string", "Comma-separated tags.", False),
            "priority": ("integer", "Lower is offered first (default 100).", False),
            "line": (
                "string",
                "Release line this lands on (a name from [flow.lines], or the current "
                "line). Omit for the current line; tasks inherit a phase's line.",
                False,
            ),
            "lines": (
                "string",
                "Release lines a FIX must reach, e.g. '1,2,3': written where [flow].port_strategy says, plus a port task `<id>@<line>` per other line.",
                False,
            ),
            "port_of": (
                "string",
                "Earlier fix this follows up: reuses the lines it reached.",
                False,
            ),
            "readd": (
                "boolean",
                "File a REMOVED item's id again with this definition. An id still in the "
                "queue is always refused -- change that item with ddflow_update.",
                False,
            ),
        },
        "api": lambda repo, a, agent: _api().task_add(
            repo,
            a["id"],
            title=a.get("title", "") or "",
            parent=a.get("parent") or a.get("phase", "") or "",
            needs=a.get("needs", "") or "",
            globs=a.get("globs", "") or "",
            body=a.get("body", "") or "",
            tags=a.get("tags", "") or "",
            priority=int(a.get("priority") or _api().DEFAULT_PRIORITY),
            line=a.get("line", "") or "",
            lines=a.get("lines", "") or "",
            port_of=a.get("port_of", "") or "",
            readd=bool(a.get("readd")),
            answer=_answer(a),
            agent=agent,
        ),
        "payload": ("id", "line", "ports", "port_strategy", "defaulted"),
    },
    "ddflow_lesson_verify": {
        "description": (
            "Re-scan every lesson that declared a code `pattern` and report the sites where "
            "it has REAPPEARED. Exit 1 names them; exit 2 means no lesson declares a "
            "pattern, which is NOT a pass — it means this project has no mechanical ratchet "
            "on its lessons yet. Run after a change that touches code a lesson governs."
        ),
        "properties": {},
        "api": lambda repo, a, agent: _api().lessons_verify(repo, agent=agent),
        "payload": "text",
        "text": True,
    },
    "ddflow_lesson_add": {
        "description": (
            "Record a lesson so it is never re-learned. Use after any bug, any operator "
            "correction, any surprise. Make the rule transferable — a future agent on a "
            "different task must be able to apply it."
        ),
        "properties": {
            "id": (
                "string",
                "Stable id you choose. Referenced by `supersedes`, by commit messages and by the reconstruction; a generated id cannot be cited in advance.",
                False,
            ),
            "title": ("string", "The rule as a one-line statement.", True),
            "rule": ("string", "The rule in full.", False),
            "why": ("string", "Why it is true / what went wrong.", False),
            "how": ("string", "How to apply or detect it.", False),
            "summary": (
                "string",
                "The lesson in ONE paragraph, for a reader who will not open the full rule. Rendered into docs/ddflow/LESSONS-SUMMARY.md.",
                False,
            ),
            "tags": ("string", "Comma-separated tags.", False),
            "seen_in": (
                "string",
                "Comma-separated item ids where this was hit. What makes a lesson "
                "checkable later instead of merely memorable.",
                False,
            ),
            "supersedes": (
                "string",
                "Comma-separated lesson ids this replaces. The old one is retired, not deleted: the corpus stops growing without losing what was once believed.",
                False,
            ),
            "pattern": (
                "string",
                "A regex naming the mistake in CODE. Scans now and stores WHICH sites match, so `ddflow_lesson_verify` can name those that reappear. Prefer it to a remembered rule when mechanical: a count says 'worse', never 'which'. Refused if it does not compile.",
                False,
            ),
            "globs": (
                "string",
                "Comma-separated globs to scan for `pattern`. Default: every tracked file.",
                False,
            ),
        },
        "api": lambda repo, a, agent: _api().lesson_add(
            repo,
            _api().LessonDraft(
                title=a.get("title", "") or "",
                rule=a.get("rule", "") or "",
                why=a.get("why", "") or "",
                how=a.get("how", "") or "",
                summary=a.get("summary", "") or "",
                tags=a.get("tags", "") or "",
                seen_in=a.get("seen_in", "") or "",
                supersedes=a.get("supersedes", "") or "",
                pattern=a.get("pattern", "") or "",
                globs=a.get("globs", "") or "",
                id=a.get("id", "") or "",
                answer=_answer(a),
            ),
            agent=agent,
        ),
        "payload": ("id",),
    },
    "ddflow_lesson_search": {
        "description": (
            "Search past lessons by relevance (BM25). Use before starting "
            "work, and whenever something surprises you."
        ),
        "properties": {
            "query": ("string", "What you are about to do, in words.", True),
            "limit": ("integer", "Max results (default 5).", False),
        },
        "api": lambda repo, a, agent: _api().lesson_search(
            repo, a.get("query", "") or "", limit=a.get("limit"), agent=agent
        ),
        "payload": "hits",
    },
    "ddflow_research_add": {
        "description": (
            "Record a research finding. verdict MUST be CONFIRMED, REFUTED or "
            "THEORETICAL, and CONFIRMED/REFUTED require a probe — a verdict with no "
            "probe behind it is an opinion. A REFUTED entry is as valuable as an "
            "adopted one: it stops the next session re-researching it."
        ),
        "properties": {
            "id": (
                "string",
                "Stable id you choose. Referenced by `supersedes`, by commit messages and by the reconstruction; a generated id cannot be cited in advance.",
                False,
            ),
            "question": ("string", "What was asked.", True),
            "verdict": ("string", "CONFIRMED | REFUTED | THEORETICAL", True),
            "claim": ("string", "The falsifiable claim.", False),
            "falsifier": ("string", "The single observation that would kill it.", False),
            "probe": ("string", "The command you ran.", False),
            "probe_output": ("string", "Its output, verbatim.", False),
            "sources": ("string", "Comma-separated URLs/DOIs you actually opened.", False),
            "mechanism": (
                "string",
                "WHY it would work in this repo. The middle field of the triple — "
                "claim, mechanism, falsifier — and the one most often skipped.",
                False,
            ),
            "budget": ("string", "What you allowed yourself, e.g. '30 min, no GPU'.", False),
            "item": ("string", "The task this research is for.", False),
        },
        "api": lambda repo, a, agent: _api().research_add(
            repo,
            _api().ResearchFinding(
                question=a.get("question", "") or "",
                verdict=a.get("verdict", "THEORETICAL") or "THEORETICAL",
                claim=a.get("claim", "") or "",
                mechanism=a.get("mechanism", "") or "",
                falsifier=a.get("falsifier", "") or "",
                probe=a.get("probe", "") or "",
                probe_output=a.get("probe_output", "") or "",
                sources=a.get("sources", "") or "",
                budget=a.get("budget", "") or "",
                item=a.get("item", "") or "",
                id=a.get("id", "") or "",
                answer=_answer(a),
            ),
            agent=agent,
        ),
        "payload": ("id", "verdict"),
    },
    "ddflow_bug_fixed": {
        "description": (
            "Close a bug. Requires the name of the regression test that would "
            "catch it again — write the test, watch it FAIL against the "
            "unfixed code, then close."
        ),
        "properties": {
            "id": ("string", "Bug id.", True),
            "regression_test": (
                "string",
                "Test that now guards this. Several: separate them with ',' or ';', or "
                "pass regression_tests.",
                False,
            ),
            "regression_tests": (
                "array",
                "The tests that now guard this, one per element -- the list form of "
                "regression_test. One of the two is required.",
                False,
            ),
            "lesson_title": ("string", "Capture a lesson at the same time.", False),
            "lesson_rule": (
                "string",
                "The lesson in full — the transferable rule, not the incident. A "
                "future agent on a different task has to be able to apply it.",
                False,
            ),
            "lesson": ("string", "Id of an EXISTING lesson this bug belongs to.", False),
            "changelog": ("string", "Optional 'Fixed: text' (any category), or skip.", False),
        },
        "api": lambda repo, a, agent: _api().bug_fixed(
            repo,
            a["id"],
            regression_test=_regression_tests(a),
            lesson=a.get("lesson", "") or "",
            lesson_title=a.get("lesson_title", "") or "",
            lesson_rule=a.get("lesson_rule", "") or "",
            changelog=a.get("changelog", "") or "",
            agent=agent,
        ),
        "payload": ("id",),
    },
    "ddflow_bug_invalid": {
        "description": (
            "Close a bug as a FALSE finding -- nothing was broken, so nothing was fixed. "
            "Never counts as a fix. Requires the reason; give the probe or test that "
            "showed it false as evidence. Refused (exit 3) for an unknown id or a bug "
            "already closed; a real bug is closed with `ddflow_bug_fixed` instead. "
            "`reopen`: instead UNDO a closure (fixed or invalid) made by mistake."
        ),
        "properties": {
            "id": ("string", "Bug id.", True),
            "reason": ("string", "Why the finding is false (with reopen: why reopen).", True),
            "evidence": (
                "string",
                "The probe command or test node id that showed it false.",
                False,
            ),
            "reopen": ("boolean", "Reopen the closed bug instead.", False),
        },
        "api": lambda repo, a, agent: (
            _bug_reopen(repo, a, agent=agent)
            if _reopening(a)
            else _api().bug_invalid(
                repo,
                a["id"],
                reason=a.get("reason", "") or "",
                evidence=a.get("evidence", "") or "",
                agent=agent,
            )
        ),
        "payload": lambda a: (
            ("id", "was", "reason_given", "fix_task", "fix_task_state", "previous_fix_task", "next")
            if _reopening(a)
            else (
                "id",
                "invalid_reason",
                "evidence",
                "unchecked",
                "fix_task",
                "fix_task_removed",
                "fix_task_kept",
            )
        ),
    },
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
    "ddflow_recall": {
        "description": (
            "'HAVE WE BEEN HERE BEFORE?' -- one search across everything this project remembers: decisions, lessons, research verdicts, operational memories, past bugs, similar tasks and the operator's earlier prompts. CALL THIS BEFORE STARTING ANY NON-TRIVIAL WORK, so nothing is said or learned twice. Results are labelled by kind (a binding decision, a transferable lesson and an old prompt change what you do differently); a superseded decision names its replacement -- follow that."
        ),
        "properties": {
            "query": ("string", "What you are about to do, in plain words.", True),
            "limit": ("integer", "Hits per source (default 3).", False),
            "max_chars": (
                "integer",
                "Total budget for the answer. The point of a budget is that recall is "
                "called at the START of work, where a long answer costs the context the "
                "work itself needs.",
                False,
            ),
            "sources": (
                "string",
                "Comma-separated subset: decisions,lessons,memories,research,bugs,"
                "items,prompts. Default: all.",
                False,
            ),
        },
        "api": lambda repo, a, agent: _api().recall(
            repo,
            a.get("query", "") or "",
            sources=a.get("sources", "") or "",
            limit=int(a.get("limit") or 3),
            max_chars=int(a.get("max_chars") or 4000),
            agent=agent,
        ),
        "payload": "results",
    },
    "ddflow_similar": {
        "description": (
            "'IS THIS ALREADY FILED?' -- the existing records most like a text, BEFORE you file it as a bug, task, lesson or other record. Read-only. Candidates cross kinds and include closed records (a bug that repeats a fixed one is caught); each carries id, kind, title, state, score (0-1), shared words and flags, per [dedupe] show_floor, max_candidates and kinds. A score is a prompt to LOOK, not a verdict. Nothing close: exit 2."
        ),
        "properties": {
            "text": (
                "string",
                "The title or summary of the record you are about to file.",
                True,
            ),
            "kind": (
                "string",
                "Comma-separated subset of the configured kinds to look in: bug,task,"
                "phase,lesson,decision,research,memory. Default: all of them.",
                False,
            ),
        },
        "api": lambda repo, a, agent: _api().similar(
            repo, a.get("text", "") or "", kinds=a.get("kind", "") or "", agent=agent
        ),
        "payload": "candidates",
    },
    "ddflow_dupes": {
        "description": (
            "'IS ANYTHING FILED TWICE?' -- the near-duplicate PAIRS already in the log, "
            "skipping pairs already linked or dismissed (a `distinct` verdict never "
            "returns). Read-only. Each pair carries both ids and kinds, their titles and "
            "the score (0-1). A score is a prompt to LOOK, not a verdict; settle a pair "
            "with ddflow_link. Nothing close: exit 2."
        ),
        "properties": {
            "kind": (
                "string",
                "Comma-separated subset of the configured kinds: bug,task,phase,lesson,"
                "decision,research,memory. Default: all of them.",
                False,
            ),
            "open_only": (
                "boolean",
                "Only pairs where both records are still live (the dedupe_sweep pass).",
                False,
            ),
            "floor": (
                "number",
                "Minimum score to report (default: [dedupe].show_floor).",
                False,
            ),
            "limit": ("integer", "At most N pairs (0 = all).", False),
        },
        "api": lambda repo, a, agent: _api().dupes(
            repo,
            kinds=a.get("kind", "") or "",
            open_only=bool(a.get("open_only")),
            floor=a.get("floor"),
            limit=int(a.get("limit") or 0),
            agent=agent,
        ),
        "payload": ("pairs", "count", "kinds", "open_only", "floor", "limit"),
    },
    "ddflow_link": {
        "description": (
            "Settle a near-duplicate pair: say how record `subject` relates to record "
            "`target`. `duplicate_of` / `extends` link them -- and MERGE two lessons (the "
            "target keeps both texts' tags and seen_in; the duplicate is superseded by it); "
            "`related` links both ways; `distinct` records a DISMISSAL ('I looked, these "
            "are different') so the pair never returns. One relation per call. Nothing is "
            "closed here except a merged lesson."
        ),
        "properties": {
            "subject": ("string", "The record being related (the duplicate, for a merge).", True),
            "relation": (
                "string",
                "extends | duplicate_of | related | distinct.",
                True,
            ),
            "target": ("string", "The record it is related to.", True),
            "reason": ("string", "Why, recorded with the link.", False),
        },
        "api": lambda repo, a, agent: _api().link_record(
            repo,
            a.get("subject", "") or "",
            a.get("relation", "") or "",
            a.get("target", "") or "",
            reason=a.get("reason", "") or "",
            agent=agent,
        ),
        "payload": ("subject", "relation", "target", "merged"),
    },
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
            "Declare WHO you are on this connection before anything that writes. Call it first when 2+ agents or subagents work this repository at once: identity attributes every claim, gate outcome and review, and the tree-derived default merges several agents in one tree into one identity with no error (a review would pass independence against itself). Pick a short stable name (your role), distinct from the others'. Idempotent. A SUBAGENT sharing its parent's connection must NOT call this; it passes `as_agent` on each call instead (the CLI's `--agent`)."
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
    "ddflow_decision_add": {
        "description": (
            "Record an architectural decision so the project stays consistent and the reasoning survives: HOW the software is built (a representation, boundary, library, invariant), settled by you or the operator. ALWAYS set `globs` to the code it governs, so it reaches whoever works those files; record `alternatives` too, or they get re-proposed."
        ),
        "properties": {
            "id": (
                "string",
                "Stable id, e.g. 'D1'. Choose one: a generated id cannot be cited in advance.",
                False,
            ),
            "title": ("string", "The decision as a one-line statement.", True),
            "decision": ("string", "What was DECIDED (not what was discussed).", True),
            "context": ("string", "The forces: why a decision was needed at all.", False),
            "consequences": ("string", "What it costs, including what it makes harder.", False),
            "alternatives": ("string", "What was rejected, and why.", False),
            "globs": ("string", "Comma-separated paths this governs.", False),
            "by": ("string", "'operator' or 'agent' or a name.", False),
            "supersedes": ("string", "Comma-separated ids this replaces.", False),
            "item": ("string", "The task it arose from.", False),
            "tags": ("string", "Comma-separated tags.", False),
            "status": (
                "string",
                "proposed | accepted (default) | superseded. 'proposed' is honest about a decision the operator has not ratified.",
                False,
            ),
            "sources": (
                "string",
                "Where this came from, comma-separated: an ADR path, a URL, a commit sha (so an audit can check it exists).",
                False,
            ),
        },
        "api": lambda repo, a, agent: _api().decision_add(
            repo,
            _api().decisions.Draft(
                title=a.get("title", ""),
                decision=a.get("decision", ""),
                id=a.get("id", "") or "",
                context=a.get("context", "") or "",
                consequences=a.get("consequences", "") or "",
                alternatives=a.get("alternatives", "") or "",
                globs=a.get("globs", "") or "",
                tags=a.get("tags", "") or "",
                sources=a.get("sources", "") or "",
                status=a.get("status", "") or "accepted",
                by=a.get("by", "") or "",
                item=a.get("item", "") or "",
                supersedes=a.get("supersedes", "") or "",
                answer=_answer(a),
            ),
            agent=agent,
        ),
        # `{"id": "..."}` — what `ddflow decision add --json` has always printed.
        "payload": ("id",),
    },
    "ddflow_decision_list": {
        "description": (
            "Every architectural decision in force. Superseded ones are "
            "hidden unless you ask for them — they are kept, never deleted, "
            "because how the architecture got here is what a rebuild needs."
        ),
        "properties": {
            "all": ("boolean", "Include superseded decisions.", False),
            "limit": ("integer", "Newest decisions returned (default 25; 0 = all).", False),
        },
        "api": lambda repo, a, agent: _api().decision_list(repo, all=bool(a.get("all"))),
        "payload": "rows",
    },
    "ddflow_decision_applicable": {
        "description": (
            "The architectural decisions that govern a specific item's declared files. "
            "CALL THIS BEFORE IMPLEMENTING: it is how a decision reaches the person "
            "writing the code, without them having to know it exists. Returns "
            "project-wide decisions too."
        ),
        "properties": {"id": ("string", "Item id.", True)},
        "api": lambda repo, a, agent: _api().decision_applicable(repo, a["id"]),
        "payload": ("applicable", "project_wide"),
    },
    "ddflow_decision_supersede": {
        "description": (
            "Mark a decision replaced by a newer one. Decisions are never "
            "edited or deleted; a reversal is a new decision that names the "
            "old one."
        ),
        "properties": {
            "id": ("string", "The decision being replaced.", True),
            "by": ("string", "The decision that replaces it.", True),
            "reason": ("string", "Why it changed.", False),
        },
        "api": lambda repo, a, agent: _api().decision_supersede(
            repo, a["id"], by=a.get("by", "") or "", reason=a.get("reason", "") or "", agent=agent
        ),
        "payload": ("id", "by"),
    },
    "ddflow_replay": {
        "description": (
            "Reconstruct the project's whole decision history from the log: every "
            "operator prompt in order, every architectural decision, every research "
            "verdict, every lesson, and the shape of the queue. This is what rebuilds "
            "the project if the code is lost — it reproduces the DECISIONS, not the "
            "bytes."
        ),
        "properties": {
            "out": ("string", "Write a recovery kit to this directory.", False),
            "verify": (
                "boolean",
                "Re-resolve every recorded commit sha against this repository and report the ones that are gone: a reconstruction citing unresolvable shas is a narrative, not a record.",
                False,
            ),
        },
        "api": lambda repo, a, agent: _api().replay(
            repo, out_dir=a.get("out", "") or "", verify=bool(a.get("verify")), agent=agent
        ),
        "payload": "text",
        "text": True,
        "kind": "replay",
    },
    "ddflow_render": {
        "description": (
            "Regenerate the human-readable markdown views (queue, lessons, the "
            "one-paragraph lessons summary, research) under docs/ddflow/."
        ),
        "properties": {
            "out": ("string", "Directory for the generated views (default: docs/ddflow).", False),
            "show": (
                "string",
                "Print ONE view instead of writing files: lessons, lessons-summary, "
                "research, or board. "
                "This is what the ddflow:// resources are served from.",
                False,
            ),
        },
        "api": lambda repo, a, agent: _api().render(
            repo,
            show=a.get("show", "") or "",
            out_dir=a.get("out") or _api().DEFAULT_RENDER_DIR,
            agent=agent,
        ),
        # Two shapes, both pre-existing: `--show` returned the DOCUMENT and without it
        # the answer was the list of files written. A predicate, because which one it
        # is cannot be known until the call.
        "payload": lambda a: "text" if a.get("show") else ("files",),
        "text": lambda a: bool(a.get("show")),
        "kind": "render",
    },
    "ddflow_list": {
        "description": (
            "Read-only lists, newest first, 25 rows unless `limit` (0 = the most: 1000, search 200); a cut says so. "
            "`kind`: task|phase|bug|research|session|search. Bugs: open unless `all`/`state`. "
            "History: ddflow_history."
        ),
        "properties": {
            "kind": ("string", "task|phase|bug|research|session|search", True),
            "id": ("string", "kind=session: one session in full.", False),
            "query": ("string", "kind=search: text to find.", False),
            "state": ("string", "Only this state.", False),
            "phase": ("string", "Only under this phase id.", False),
            "tag": ("string", "Only this tag.", False),
            "owner": ("string", "Only this agent's rows.", False),
            "since": ("string", "Changed at/after this ISO date.", False),
            "limit": ("integer", "Rows (default 25; 0 = the most: 1000, search 200).", False),
            "all": ("boolean", "kind=bug: include fixed/invalid.", False),
            "mode": ("string", "search: ranked|exact|regex.", False),
            "sources": ("string", "search: comma-separated record kinds.", False),
        },
        "api": lambda repo, a, agent: _api().view_read(
            repo,
            a["kind"],
            id=a.get("id", "") or "",
            query=a.get("query", "") or "",
            state=a.get("state", "") or "",
            phase=a.get("phase", "") or "",
            tag=a.get("tag", "") or "",
            owner=a.get("owner", "") or "",
            since=a.get("since", "") or "",
            limit=1000 if a.get("limit") == 0 else int(a.get("limit") or 25),
            all=bool(a.get("all")),
            mode=a.get("mode", "") or "ranked",
            sources=a.get("sources", "") or "",
        ),
        "payload": "",
    },
    "ddflow_rebuild": {
        "description": (
            "Re-derive the search index from the event log. The index is a "
            "disposable cache; this is never a data-loss operation."
        ),
        "properties": {},
        "api": lambda repo, a, agent: _api().rebuild(repo, agent=agent),
        "payload": ("events", "items"),
    },
    "ddflow_history": {
        "description": (
            "ONE timeline of everything that happened: claims, releases, gates, bugs, "
            "decisions, lessons, completions. Other views say what is true now; this says "
            "how it got that way.\n\n"
            "Filter with `item` (one task's life), `kind` (a family: 'gate', "
            "'lease.acquired', 'decision,bug'), `since`, `by_agent`. Exit 2 means nothing "
            "matched: an answer, not a failure."
        ),
        "properties": {
            "item": ("string", "Restrict to one item's timeline.", False),
            "kind": (
                "string",
                "Comma-separated event kinds or families: 'gate', 'lease.acquired', "
                "'decision,bug'.",
                False,
            ),
            "since": ("string", "ISO timestamp lower bound.", False),
            "limit": ("integer", "Most recent N entries (default 40).", False),
            "tail": ("integer", "Last N entries, oldest first (overrides limit).", False),
            "by_agent": ("string", "Only this agent's events.", False),
        },
        "api": lambda repo, a, agent: _api().history(
            repo,
            item=a.get("item", "") or "",
            kind=a.get("kind", "") or "",
            since=a.get("since", "") or "",
            limit=int(a.get("limit") or 40),
            agent=agent,
            by_agent=a.get("by_agent", "") or "",
            tail=int(a.get("tail") or 0),
        ),
        "payload": ("total", "shown", "events"),
    },
    "ddflow_import": {
        "description": (
            "For a project that ALREADY HAS HISTORY and is adopting ddflow now: reads its todo checklists, lessons corpus, ADR files and unmerged branches and proposes them as queue items. Reports by default; writes NOTHING until `apply` is true. Call it right after `ddflow_setup` on any repository that is not brand new. The proposal is a GUESS: the `import-existing-project` prompt walks through fixing it. Exit 2: nothing found."
        ),
        "properties": {
            "apply": ("boolean", "Write the proposal. Default false: look first.", False),
            "include_done": (
                "boolean",
                "Also import already-ticked items as completed. Off by default — a "
                "finished history is not a queue, and one real project yielded 3,638 of "
                "them.",
                False,
            ),
            "max_tasks": (
                "integer",
                "Refuse to propose more tasks than this (default 200).",
                False,
            ),
        },
        "api": lambda repo, a, agent: _api().import_project(
            repo,
            apply=bool(a.get("apply")),
            include_done=bool(a.get("include_done")),
            max_tasks=int(a.get("max_tasks") or 0),
            agent=agent,
        ),
        "payload": "",
    },
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
            repo, a["which"], a["gates"], dry_run=bool(a.get("dry_run"))
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
        ),
        "payload": ("gate", "changed", "applied"),
    },
    "ddflow_workflow_drop": {
        "description": (
            "Take a gate out of both pipelines, and out of `required`, so it does not become a requirement that requires nothing. WRITES config. The gate's DEFINITION stays, so putting it back is one call. Exit 2: it was in neither pipeline. Ask the operator first: a gate in a pipeline is a check somebody added on purpose."
        ),
        "properties": {
            "id": ("string", "The gate id to remove from the pipelines.", True),
            "dry_run": ("boolean", "Report the change and write nothing.", False),
        },
        "api": lambda repo, a, agent: _api().workflow_drop(
            repo, a["id"], dry_run=bool(a.get("dry_run"))
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
    "ddflow_verify": {
        "description": "Re-check a done task's claims; fails if one does not hold. No id: sweep all, worst first.",
        "properties": {
            "id": ("string", "Task id; omit to sweep.", False),
            "phase": ("string", "Sweep only this phase.", False),
            "limit": ("integer", "How many of the worst to list (20).", False),
            "file_bugs": ("boolean", "File a bug per failing completion.", False),
            "reopen": ("boolean", "Reopen a failing completion.", False),
            "reason": ("string", "Why (reopen).", False),
            "force": ("boolean", "Reopen even if it holds.", False),
            "pack": ("boolean", "Evidence pack for a verifier.", False),
            "judge": ("boolean", "Cross-family reviewer judges it.", False),
        },
        "api": lambda repo, a, agent: _api().verify_tool(
            repo,
            id=a.get("id", "") or "",
            phase=a.get("phase", "") or "",
            limit=int(a["limit"]) if a.get("limit") is not None else None,
            file_bugs=bool(a.get("file_bugs")),
            reopen=bool(a.get("reopen")),
            reason=a.get("reason", "") or "",
            force=bool(a.get("force")),
            pack_=bool(a.get("pack")),
            judge_=bool(a.get("judge")),
            agent=agent,
        ),
        "payload": "",
    },
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
            repo, topic=a.get("topic", "") or "", tools=TOOLS, agent=agent
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
    "ddflow_companions": {
        "description": (
            "Which companion MCP servers serve this project's gates, which are installed, which an agent launches. Without them, agent gates pass on assertion. Exit 2: a default one is missing or unregistered. Read-only; never installs or launches."
        ),
        "properties": {
            "no_probe": ("boolean", "Skip the detection probes (faster, less certain).", False),
        },
        "api": lambda repo, a, agent: _api().companions_list(
            repo, no_probe=bool(a.get("no_probe")), agent=agent
        ),
        "payload": ("companions", "gate_coverage", "uncovered_gates"),
    },
    "ddflow_bisect": {
        "description": (
            "Which earlier test file makes `victim` fail only in full-suite order? Delta-debugs the files before it, running `cmd` many times. Exit 2: nothing to report."
        ),
        "properties": {
            "victim": ("string", "Failing test id.", True),
            "cmd": ("string", "Test command; {tests} = the list.", True),
            "candidates": ("string", "Comma-separated files in run order.", False),
            "timeout": ("integer", "Seconds per run (600).", False),
        },
        "api": lambda repo, a, agent: _bisect(repo, a),
        "payload": ("state", "victim", "polluters", "candidates", "summary", "runs"),
    },
    "ddflow_companions_verify": {
        "description": (
            "Launch MCP companions and require a JSON-RPC answer to `initialize`. SPAWNS processes (opt-in). Default: registered or installed ones. Exit 1: not an MCP server; 2: unsure."
        ),
        "properties": {
            "id": ("string", "Comma-separated ids, installed or not.", False),
        },
        "api": lambda repo, a, agent: _api().companions_verify(
            repo, a.get("id", "") or "", agent=agent
        ),
        "payload": ("verified", "skipped"),
    },
    "ddflow_companions_add": {
        "description": (
            "WRITES the agent config: registers companion MCP servers that are ALREADY installed (exit 3 for one that is not). dry_run=true FIRST; show the operator the entry: which servers an agent launches is their decision."
        ),
        "properties": {
            "id": ("string", "Comma-separated ids; default: every installed one.", False),
            "agents": ("string", "Comma-separated agent keys (default: claude).", False),
            "dry_run": (
                "boolean",
                "Report the exact entry that would be written; write nothing.",
                False,
            ),
        },
        "api": lambda repo, a, agent: _api().companions_add(
            repo,
            _api().Registration(
                ids=a.get("id", "") or "",
                agents=a.get("agents", "") or "",
                force=bool(a.get("force")),
                dry_run=bool(a.get("dry_run")),
            ),
            agent=agent,
        ),
        "payload": ("actions", "applied", "written", "refused"),
    },
    "ddflow_bug_found": {
        "description": (
            "Report a bug the moment you find it, BEFORE fixing it. Recording it first "
            "makes the fix accountable: `ddflow_bug_fixed` refuses to close one without "
            "a regression test. A hunt that records nothing looks like one that found nothing."
        ),
        "properties": {
            "id": ("string", "Stable id, e.g. 'B1'. You will cite it when closing.", False),
            "summary": ("string", "What is wrong, in one line.", True),
            "item": ("string", "The task it was found in or affects.", False),
            "title": ("string", "Short headline.", False),
            "severity": ("string", "low|medium|high|critical.", False),
            "scope": ("string", "project (default) or ddflow.", False),
            "globs": ("string", "The fix task's files; default: the item's globs.", False),
            "no_task": ("boolean", "File no fix task (fixed in the same commit).", False),
        },
        "api": lambda repo, a, agent: _api().bug_found(
            repo,
            summary=a.get("summary", "") or "",
            item=a.get("item", "") or "",
            id=a.get("id", "") or "",
            title=a.get("title", "") or "",
            severity=a.get("severity", "") or "",
            scope=a.get("scope", "") or "",
            globs=a.get("globs", "") or "",
            no_task=bool(a.get("no_task")),
            answer=_answer(a),
            agent=agent,
        ),
        "payload": ("id", "offer", "fix_task", "fix_task_filed"),
    },
    "ddflow_bug_file_tasks": {
        "description": (
            "File a fix task for every open bug that has none (one-shot after an upgrade; "
            "`ddflow_bug_found` files one per bug by default)."
        ),
        "properties": {
            "dry_run": ("boolean", "List what would be filed; write nothing.", False),
        },
        "api": lambda repo, a, agent: _api().bug_file_tasks(
            repo, dry_run=bool(a.get("dry_run")), agent=agent
        ),
        "payload": ("filed", "linked", "tasks", "links", "dry_run"),
    },
    "ddflow_session_note": {
        "description": (
            "Record something that happened during a session which is neither an "
            "operator prompt nor a decision — a surprise, a dead end, why you changed "
            "approach. It goes into the reconstruction alongside the prompts, and a "
            "dead end recorded is a dead end nobody walks down twice."
        ),
        "properties": {
            "session": (
                "string",
                "Session id; omit for the latest open.",
                False,
            ),
            "text": ("string", "The note.", True),
            "item": ("string", "Item it concerns.", False),
        },
        "api": lambda repo, a, agent: _api().session_note(
            repo,
            a.get("session", "") or "",
            a.get("text", "") or "",
            item=a.get("item", "") or "",
            agent=agent,
        ),
        "payload": ("session", "how"),
    },
    "ddflow_session_end": {
        "description": (
            "Close a session with a summary of what it achieved. The summary is what a "
            "later reader sees before deciding whether to open the whole transcript, "
            "so write it for someone who was not there."
        ),
        "properties": {
            "session": ("string", "Session id.", True),
            "summary": ("string", "What this session achieved.", False),
        },
        "api": lambda repo, a, agent: _api().session_end(
            repo, a.get("session", "") or "", summary=a.get("summary", "") or "", agent=agent
        ),
        "payload": ("session",),
    },
    "ddflow_decision_show": {
        "description": (
            "Read ONE architectural decision in full — its context, what was decided, "
            "the consequences, and what was rejected. `ddflow_decision_list` gives "
            "you the titles; this is what you read before working against one, and "
            "especially before proposing something it already considered."
        ),
        "properties": {"id": ("string", "Decision id.", True)},
        "api": lambda repo, a, agent: _api().decision_show(repo, a["id"]),
        "payload": "decision",
    },
    "ddflow_prompts": {
        "description": (
            "Inspect the prompt templates this project uses, and where each comes from "
            "(shipped default, project override, or an explicit config path). Use "
            "`eject` to copy the shipped ones into .ddflow/prompts/ so the project can "
            "edit them as plain text — reviewer instructions and workflow commands are "
            "operator-tunable behaviour, not code."
        ),
        "properties": {
            "action": ("string", "list (default), show, or eject.", False),
            "name": ("string", "Template name, for show/eject.", False),
        },
        "api": lambda repo, a, agent: _api().prompts(
            repo,
            action=a.get("action", "list") or "list",
            name=a.get("name", "") or "",
            agent=agent,
        ),
        # Prose for SOME arguments, like `render`: `show` returns the template TEXT and
        # `eject` the files it wrote, while `list` is a table callers parse.
        "payload": lambda a: "text" if a.get("action") in ("show", "eject") else "rows",
        "text": lambda a: a.get("action") in ("show", "eject"),
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
    "ddflow_doctor": {
        "description": (
            "Integrity and health check: log corruption, dependency cycles, "
            "unknown dependencies, orphaned worktrees, stale index."
        ),
        "properties": {},
        "api": lambda repo, a, agent: _api().doctor(repo, agent=agent),
        # PROSE, as it has always been: a list of problems with advice attached is
        # what an operator and an agent both want, and `views/human.py` renders it once
        # for both.
        "payload": "text",
        "text": True,
        "kind": "doctor",
    },
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
            "AFTER EACH CHANGE: the tests your change reaches (changed tests, tests importing a changed module directly or one step removed, tests named after it, a changed conftest's), each with why, and a command running them IN PARALLEL. Run it; do not reason about which matter. Never a pass: unit_tests runs the WHOLE suite. `item`: diff that item's worktree. Exit 2: no test reaches the change."
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
    "ddflow_session_prompt": {
        "description": (
            "Record the operator's prompt verbatim. This is what makes the project "
            "reconstructible from the log alone if everything else is lost. Secrets are "
            "redacted before anything touches disk. Call it once per operator turn."
        ),
        "properties": {
            "session": (
                "string",
                "Session id; omit for the latest open.",
                False,
            ),
            "text": ("string", "The prompt, verbatim.", True),
            "item": ("string", "Item it concerns.", False),
        },
        "api": lambda repo, a, agent: _api().session_prompt(
            repo,
            a.get("session", "") or "",
            a.get("text", "") or "",
            item=a.get("item", "") or "",
            agent=agent,
        ),
        "payload": ("redactions", "session", "how"),
    },
    "ddflow_setup": {
        "description": (
            "Install ddflow into this repository: creates .ddflow/, writes the driver "
            "and the AGENTS.md section, and registers nothing else. Run this ONCE per "
            "project, then set your test command with ddflow_configure. Safe to re-run "
            "— it updates a managed block and leaves your own prose alone."
        ),
        "properties": {
            "agents": (
                "string",
                # GENERATED from the registry. Hand-kept copies of this list have
                # drifted twice; an agent reads this spec to decide what it may pass.
                "Comma-separated agents to write driver deltas for: "
                f"{','.join(_AGENT_KEYS())}. Default: all.",
                False,
            ),
            "refresh_docs": (
                "boolean",
                "Rewrite ONLY the driver docs, AGENTS.md/CLAUDE.md blocks and adopted agents' native rules from this ddflow's templates (no MCP, hook or command-file changes), e.g. when ddflow_doctor notes drift. Refused on a never-adopted project.",
                False,
            ),
        },
        # Where the server stands decides where the committed files go: a linked
        # worktree's own checkout, not the shared primary (bug B1e7ad10c6c). A foreign
        # `as_agent` is not standing in the connection's tree (B11e4c5a185), so it is
        # asked from the primary, as from a CLI run there -- never the parent's branch.
        "wants_called_from": True,
        "api": lambda repo, a, agent, called_from=None: _api().adopt_project(
            repo,
            _api().Adoption(
                agents=a.get("agents", "") or "", refresh_docs=bool(a.get("refresh_docs"))
            ),
            agent=agent,
            called_from=called_from,
        ),
        # PROSE: a checklist of what it wrote and what to do next.
        "payload": "text",
        "text": True,
        "kind": "setup",
    },
    "ddflow_configure": {
        "description": (
            'Read or write .ddflow/config.toml (WRITES). No arguments: every knob with value, source and meaning. `set` edits one dotted key in place (preferred); `toml` APPENDS a fragment, e.g.\n[gate.unit_tests]\ncommand = "pytest -q -n auto"\n(needs pytest-xdist). The committed file is generic project policy; anything of THIS machine or operator (a reviewer endpoint, a host, a key variable, worker counts) goes with local=true to the git-ignored .ddflow/local/config.toml, read last and never committed. A reviewer there is a `[[reviewer]]` block; `ddflow_reviewers_detect` write=true writes one.'
        ),
        "properties": {
            "set": (
                "string",
                "Dotted key to set, e.g. 'gate.unit_tests.command'. Preferred: it "
                "edits in place and works whether or not the section exists.",
                False,
            ),
            "value": ("string", "The value for `set`.", False),
            "toml": (
                "string",
                "A whole TOML block to append. Fails if it would "
                "duplicate an existing table — use `set` instead then.",
                False,
            ),
            "filter": ("string", "Only show knobs whose name contains this.", False),
            "local": (
                "boolean",
                "Write `set`/`toml` to the git-ignored .ddflow/local/config.toml instead "
                "of the committed config: for this machine's endpoints, hosts, key "
                "variables and sizing.",
                False,
            ),
        },
        "api": lambda repo, a, agent: _configure_reported(repo, a, agent),  # noqa: PLW0108 -- defined below the table
        # PROSE, and `explain=True`: the string path was `config --explain`, which is
        # every knob with its documentation AND its source. The source is the half an
        # operator debugging a setting cannot do without.
        "payload": "text",
        "text": True,
        "kind": "config",
    },
    "ddflow_reviewers_detect": {
        "description": (
            "Probe well-known local ports for an OpenAI-compatible model server (ollama, vLLM, LM Studio, llama.cpp, sglang) and report what serves, with each model's pretraining family: how to find a reviewer from a DIFFERENT family than yourself, which the critic gate requires. write=true records it in the git-ignored .ddflow/local/reviewers.toml (this machine's, never committed)."
        ),
        "properties": {
            "write": (
                "boolean",
                "Append the discovered reviewers to .ddflow/local/reviewers.toml.",
                False,
            ),
            "shared": (
                "boolean",
                "With write: commit them to .ddflow/config.toml instead, for every "
                "clone. Only for a reviewer the whole team reaches at the same address.",
                False,
            ),
        },
        "api": lambda repo, a, agent: _api().reviewers_detect(
            repo, write=bool(a.get("write")), shared=bool(a.get("shared")), agent=agent
        ),
        "payload": "text",
        "text": True,
        "kind": "reviewers.detect",
    },
    "ddflow_reviewers_list": {
        "description": "Show the configured reviewers, their families and which gates they serve.",
        "properties": {},
        "api": lambda repo, a, agent: _api().reviewers_list(repo, agent=agent),
        "payload": "text",
        "text": True,
        "kind": "reviewers.list",
    },
    "ddflow_review": {
        "description": (
            "Run the configured cross-family reviewer over an item's diff and record the "
            "result: the critic gate, performed by ddflow. "
            "No reviewer, endpoint or verdict records UNAVAILABLE, never a pass. A gate gets [review].max_rounds (default 2) full rounds, then "
            "delta=true and ddflow_review_triage."
        ),
        "properties": {
            "id": ("string", "Item whose diff to review.", True),
            "gate": ("string", "Gate: critic (default) or rubber_duck.", False),
            "intent": (
                "string",
                "What the change is MEANT to do. The reviewer flags where the diff "
                "and the intent disagree, so without it there is nothing to "
                "disagree with. Defaults to the item's title and body.",
                False,
            ),
            "base": ("string", "Ref to diff against (default: the base branch).", False),
            "context": ("string", "Extra context for the reviewer.", False),
            "commit": (
                "string",
                "Review this ONE landed commit (against its first parent) instead of the "
                "item's branch: the after-merge review, when the branch is gone.",
                False,
            ),
            "branch": (
                "string",
                "Review this branch against base, for an item claimed with no_worktree (default: the branch checked out where this connection runs). With neither, the review is recorded unavailable.",
                False,
            ),
            "delta": (
                "boolean",
                "Recheck only what changed since the reviewed head.",
                False,
            ),
            "full": (
                "boolean",
                "Force a full round (else a delta once reviewed).",
                False,
            ),
            "chunk": (
                "array",
                "Re-review ONLY these chunk numbers (as the recorded review numbered them, "
                "e.g. [5]) and merge into that record; needs the same diff, chunk size and reviewer.",
                False,
            ),
        },
        "api": lambda repo, a, agent, called_from=None, on_progress=None: _api().run_review(
            repo,
            on_progress=on_progress,
            gate=a.get("gate") or "critic",
            item=a.get("id", "") or "",
            intent=a.get("intent", "") or "",
            context=a.get("context", "") or "",
            base=a.get("base", "") or "",
            commit=a.get("commit", "") or "",
            branch=a.get("branch", "") or "",
            called_from=called_from,
            agent=agent,
            chunks=a.get("chunk") or None,
            delta=bool(a.get("delta")),
            full=bool(a.get("full")),
        ),
        "wants_called_from": True,
        "wants_progress": True,
        # The TRANSCRIPT the run produced — findings already formatted with their
        # severities, which is what this tool has always returned.
        "payload": "text",
        "text": True,
        "kind": "review",
    },
    "ddflow_review_triage": {
        "description": (
            "Record your triage of ONE finding of an item's recorded `ddflow review`: it is "
            "refuted (probe = the run that shows it false) or confirmed (probe = the fix or "
            "test that answers it). The finding number is the #N the review printed. The "
            "gate stays failed -- a review that reported findings is not re-recorded as "
            "passed; the log shows each finding's fate instead (decision D-review-triage)."
        ),
        "properties": {
            "id": ("string", "The item whose review it is.", True),
            "gate": ("string", "Omit if one gate has findings.", False),
            "finding": (
                "integer",
                "The finding's number: #N in the review's output -- of the RECORDED "
                "reviewer's findings, which the output's last lines name when several ran.",
                True,
            ),
            "verdict": ("string", "refuted or confirmed.", True),
            "probe": ("string", "The evidence for the verdict. Required.", True),
        },
        "api": lambda repo, a, agent: _api().review_triage(
            repo,
            a.get("id", "") or "",
            gate=a.get("gate") or "",
            finding=int(a.get("finding") or 0),
            verdict=a.get("verdict", "") or "",
            probe=a.get("probe", "") or "",
            agent=agent,
        ),
        "payload": "text",
        "text": True,
        "kind": "review.triage",
    },
    "ddflow_show": {
        "description": (
            "Everything known about one phase, task or bug (a bug id works too): state, dependencies, declared "
            "globs, the lease and who holds it, the worktree path you can cd to, and "
            "every gate's outcome with its evidence. Use it to check your own work "
            "before calling ddflow_complete."
        ),
        "properties": {"id": ("string", "Item id (phase, task) or bug id.", True)},
        "api": lambda repo, a, agent: _api().show(repo, a["id"], agent=agent),
        "payload": "item",
    },
    "ddflow_update": {
        "description": (
            "Change an item's fields. MOST IMPORTANT USE: widening `globs` when your "
            "work turns out to touch files outside what you claimed. Do that BEFORE "
            "writing them: the conflict detector and commit hook work from the declared "
            "globs, so an undeclared file is unprotected and the commit is refused."
        ),
        "properties": {
            "id": ("string", "Item id.", True),
            "globs": (
                "string",
                "Path globs this item writes, comma-separated or a JSON array. REPLACES "
                "the list (a claimed item's lease too); the result names what it dropped.",
                False,
            ),
            "needs": (
                "string",
                "Comma-separated ids it depends on. Pass an EMPTY string to clear them "
                "— that is how you break a dependency cycle the loop detector found.",
                False,
            ),
            "title": ("string", "New title.", False),
            "body": ("string", "New detail / acceptance criteria.", False),
            "tags": ("string", "Comma-separated tags.", False),
            "priority": ("integer", "Lower is offered first (default 100).", False),
            "line": ("string", "Move it to another release line.", False),
            "resources": (
                "string",
                "Physical resources the work RUNS on, beside its files: 'gpu:4,vllm-fleet'. `next` withholds and `claim` refuses while live claims use up the capacity ([schedule] resources). Declare for a GPU job, model server or long run. Empty clears.",
                False,
            ),
            "worktree": (
                "string",
                "Rebind the item and your live lease to this linked worktree (absolute, or repo-relative) and its branch: the way out of a wrong-tree binding, since re-claiming keeps the recorded tree and merge lands that tree's branch.",
                False,
            ),
        },
        # Typed, and the argv lambda that used to sit here is GONE rather than kept
        # "in case". The `api` branch runs first, so it was unreachable -- a second
        # encoding of the same operation that no test could have caught drifting,
        # because nothing executed it. That is the duplicate-then-drift shape, and
        # keeping a dead fallback is how it starts.
        #
        # `None` means leave alone and `[]` means clear, which is what
        # `_opt(clearable=True)` existed to rebuild after argv flattened both to an
        # empty string. Here the distinction is simply the values themselves.
        "api": lambda repo, a, agent: _api().update(
            repo,
            a["id"],
            _api().ItemEdit(
                title=a.get("title"),
                body=a.get("body"),
                needs=_list_or_none(a, "needs"),
                # Raw, for the api's single read: split on commas here first, a JSON
                # array (or a glob holding a comma inside one) was lost (roborev).
                globs=(
                    None
                    if a.get("globs") is None
                    else list(a["globs"])
                    if isinstance(a["globs"], list)
                    else [a["globs"]]
                ),
                tags=_list_or_none(a, "tags"),
                priority=a.get("priority"),
                line=a.get("line"),
                resources=_list_or_none(a, "resources"),
                worktree=a.get("worktree"),
            ),
            agent=agent,
        ),
    },
    "ddflow_abandon": {
        "description": (
            "Stop work on an item without completing it, with a reason. Use when a "
            "task turns out to be unnecessary or impossible. DIFFERENT from blocking: "
            "a blocked item is waiting and will resume; an abandoned one will not, and "
            "so it stops holding its phase open — which an unfinished task otherwise "
            "does forever, since nothing can ever finish it."
        ),
        "properties": {
            "id": ("string", "Item id.", True),
            "reason": ("string", "Why it is being dropped.", True),
            "force": (
                "boolean",
                "Abandon although a sub-task is still open. Those sub-tasks are NOT abandoned with it: decide about each, or they sit under a parent nobody will finish.",
                False,
            ),
        },
        "api": lambda repo, a, agent: _api().abandon(
            repo, a["id"], reason=a.get("reason", "") or "", force=bool(a.get("force")), agent=agent
        ),
        "payload": ("id", "reason"),
    },
    "ddflow_remove": {
        "description": (
            "Take an item out of the queue. The log is append-only, so this RECORDS a "
            "removal rather than erasing anything — the item stays in the history and "
            "in replay, which keeps the record honest about work that was planned and "
            "then dropped. Refuses if another item depends on it."
        ),
        "properties": {
            "id": ("string", "Item id.", True),
            "reason": ("string", "Why.", False),
            "force": (
                "boolean",
                "Remove although it still has open children or dependents. Both leave the queue inconsistent in a way the scheduler reports; read the refusal before overriding.",
                False,
            ),
        },
        "api": lambda repo, a, agent: _api().remove_item(
            repo, a["id"], reason=a.get("reason", "") or "", force=bool(a.get("force")), agent=agent
        ),
        "payload": ("id",),
    },
    "ddflow_release": {
        "description": (
            "Give up a lease without completing the item — when you are handing off, "
            "stopping, or recovering someone else's abandoned work after inspecting it. "
            "The note is recorded in the log and is often the only lasting explanation "
            "of why a claim was broken."
        ),
        "properties": {
            "id": ("string", "Item id.", True),
            "note": ("string", "Why you are releasing it.", False),
        },
        "api": lambda repo, a, agent: _api().release_item(
            repo, a["id"], note=a.get("note", "") or "", agent=agent
        ),
        "payload": ("released", "woke"),
    },
    "ddflow_wait": {
        "description": (
            "Sleep until an item can be claimed (or, with no item, until anything is ready) and return the moment it can. Use it instead of polling or asking the operator when a claim was refused for another agent's lease or overlapping files, or an unfinished dependency someone is working on. Exit 0: claim now (it says what freed it). Exit 2: deadline passed, or waiting cannot help (done, cycle, operator hold, a dependency nobody works on) and it says what to do. The holder is told you wait."
        ),
        "properties": {
            "item": ("string", "The item to wait for (default: anything ready).", False),
            "phase": ("string", "With no item: anything ready in this phase.", False),
            "kind": ("string", "'task' (default) or 'phase'.", False),
            "globs": (
                "string",
                "With item: the globs you will claim with (comma-separated or a JSON "
                "array), so ready means that claim will not be refused for them.",
                False,
            ),
            "timeout": (
                "number",
                f"Seconds to wait (default {MCP_WAIT_DEFAULT_S}, at most {MCP_WAIT_MAX_S}: "
                f"a client may time a tool call out, so call again to keep waiting). 0 asks "
                f"without waiting.",
                False,
            ),
            "poll": ("number", "Seconds between log checks (default 2).", False),
        },
        "api": lambda repo, a, agent: _api().wait_item(
            repo,
            item=a.get("item", "") or "",
            phase=a.get("phase", "") or "",
            kind=a.get("kind") or _api().DEFAULT_NEXT_KIND,
            globs=a.get("globs") or None,
            timeout_s=_wait_timeout(a),
            poll_s=a.get("poll"),
            agent=agent,
        ),
        "payload": "",
    },
    "ddflow_block": {
        "description": (
            "Mark an item blocked on something outside the queue — a decision, "
            "an upstream outage, an operator question. Better than leaving it "
            "claimed: a blocked item states its reason. A DONE or ABANDONED item needs "
            "reopen."
        ),
        "properties": {
            "id": ("string", "Item id.", True),
            "reason": ("string", "What it is waiting on.", True),
            "reopen": (
                "boolean",
                "Allow blocking a DONE or ABANDONED item.",
                False,
            ),
        },
        "api": lambda repo, a, agent: _api().block(
            repo,
            a["id"],
            reason=a.get("reason", "") or "",
            reopen=bool(a.get("reopen", False)),
            agent=agent,
        ),
        "payload": ("id",),
    },
    "ddflow_external_sync": {
        "description": (
            "Observe the items in SIBLING repositories that this queue depends on "
            "(`needs = ['run_nemo_run:132.D']`, repositories named in [schedule] repos), "
            "and record what changed in this log. An external dependency is met only "
            "once it has been observed done here, so run this before `ddflow_next` when "
            "work waits on another project. Reads the other repository; never writes it."
        ),
        "properties": {},
        "api": lambda repo, a, agent: _api().external_sync(repo, agent=agent),
        "payload": "observed",
    },
    "ddflow_job_run": {
        "description": (
            "Launch a LONG-RUNNING command for an item (a training run, a data generation, a model server) detached into its own session, so it outlives you, this server and a restarted remote-control service, and record it. Runs in the item's worktree; returns the job id, pid and log path. Then WAIT with ddflow_job_list rather than polling; `ddflow_brief` shows running jobs to the next session. Declare the item's `resources` (ddflow_update) so nobody else starts a run on the same GPUs."
        ),
        "properties": {
            "item": ("string", "Item the job is for.", True),
            "command": ("string", "The shell command.", True),
            "log": ("string", "Output file (default .ddflow/local/jobs/<item>-<t>.log).", False),
            "cwd": ("string", "Working directory (default: the item's worktree).", False),
        },
        "api": lambda repo, a, agent: _api().job_run(
            repo,
            a["item"],
            a["command"],
            log_file=a.get("log", "") or "",
            cwd=a.get("cwd", "") or "",
            agent=agent,
        ),
        "payload": ("id", "pid", "log", "cwd"),
    },
    "ddflow_job_add": {
        "description": (
            "Register a long-running process you started some other way (torchrun, a "
            "launcher script), by pid, while it runs -- so its liveness can be checked by "
            "anyone later, including after a pid is reused."
        ),
        "properties": {
            "item": ("string", "Item the job is for.", True),
            "pid": ("integer", "Its process id.", True),
            "command": ("string", "What it is running, for humans.", False),
            "log": ("string", "Where its output goes.", False),
        },
        "api": lambda repo, a, agent: _api().job_add(
            repo,
            a["item"],
            int(a["pid"]),
            command=a.get("command", "") or "",
            log_file=a.get("log", "") or "",
            agent=agent,
        ),
        "payload": ("id", "pid", "log", "cwd"),
    },
    "ddflow_job_list": {
        "description": (
            "Long-running jobs and their LIVE status: running, exited (with the exit code "
            "its log recorded), gone (killed: no exit recorded), elsewhere (another host), "
            "or ended. Use it to decide whether to keep waiting, collect results, or "
            "restart. A long run is a WAIT, never a reason to stop working the queue."
        ),
        "properties": {
            "item": ("string", "Only this item's jobs.", False),
            "all": ("boolean", "Include jobs already recorded as ended.", False),
        },
        "api": lambda repo, a, agent: _api().job_list(
            repo, item=a.get("item", "") or "", include_ended=bool(a.get("all")), agent=agent
        ),
        "payload": "jobs",
    },
    "ddflow_job_end": {
        "description": (
            "Record that a job ended and how. Refused while the process is still running. "
            "The exit code defaults to the one its log recorded."
        ),
        "properties": {
            "job": ("string", "Job id.", True),
            "exit_code": ("integer", "Override the recorded exit code.", False),
            "note": ("string", "What came of it: metrics, where the output is.", False),
            "force": (
                "boolean",
                "End a job that runs on ANOTHER host, after checking it there. Without it "
                "such a job is refused: 'could not look' is not 'not running'.",
                False,
            ),
        },
        "api": lambda repo, a, agent: _api().job_end(
            repo,
            a["job"],
            exit_code=a.get("exit_code"),
            note=a.get("note", "") or "",
            force=bool(a.get("force")),
            agent=agent,
        ),
        "payload": ("id", "exit_code"),
    },
    "ddflow_memory_add": {
        "description": (
            "Remember ONE operational fact about this machine, repository or working state ('this box has 8 H200s', 'use -n 16, never -n auto'). Shown at the top of every ddflow_brief and found by ddflow_recall, in every worktree at once. Not for rules (ddflow_lesson_add), events (ddflow_session_note) or how the software is built (ddflow_decision_add). Never a secret: the log is committed. Refused over [memory] max_chars (default 280)."
        ),
        "properties": {
            "text": ("string", "The fact, in one or two sentences.", True),
            "tags": ("string", "Comma-separated tags, e.g. 'gpu,machine'.", False),
            "id": (
                "string",
                "Re-record an existing memory under its id -- how a fact is CORRECTED. "
                "Omit for a new one.",
                False,
            ),
        },
        "api": lambda repo, a, agent: _api().memory_add(
            repo,
            a.get("text", "") or "",
            tags=a.get("tags", "") or "",
            id=a.get("id", "") or "",
            answer=_answer(a),
            agent=agent,
        ),
        "payload": ("id", "replaced"),
    },
    "ddflow_memory_list": {
        "description": (
            "The project's operational memories, newest first -- or ranked against "
            "`query`. What an agent must know before touching anything on this machine; "
            "read them at session start if ddflow_brief truncated the list."
        ),
        "properties": {
            "query": ("string", "Rank by relevance to this instead of by recency.", False),
            "limit": ("integer", "At most this many (default: all).", False),
            "all": ("boolean", "Include forgotten memories, with why they were forgotten.", False),
        },
        "api": lambda repo, a, agent: _api().memory_list(
            repo,
            query=a.get("query", "") or "",
            limit=int(a.get("limit") or 0),
            include_forgotten=bool(a.get("all")),
            agent=agent,
        ),
        "payload": ("memories", "total_live"),
    },
    "ddflow_memory_forget": {
        "description": (
            "Stop believing a memory that is no longer true. It is kept, with the reason: "
            "'we thought X until Y' is what stops the next agent re-learning X. Correct a "
            "fact instead with ddflow_memory_add and its id."
        ),
        "properties": {
            "id": ("string", "Memory id.", True),
            "reason": ("string", "Why it is no longer true.", True),
        },
        "api": lambda repo, a, agent: _api().memory_forget(
            repo, a["id"], reason=a.get("reason", "") or "", agent=agent
        ),
        "payload": ("id",),
    },
    "ddflow_resolve": {
        "description": (
            "Settle a CONTESTED item: two clones each added the same id with different content, or each claimed it, and a merge brought both in (`ddflow_doctor` names them, `ddflow_show` lists the rivals, `ddflow_next` withholds them). `keep` names the definition (event id or agent) and/or the lease holder to keep; the losing claim is released in the same transaction; a losing DEFINITION comes back in `lost`: re-add it under a new id with `refile_as`, or it stays only in the log. Refused (exit 3) when not contested."
        ),
        "properties": {
            "id": ("string", "The contested item.", True),
            "keep": (
                "string",
                "Event id (or a 6+ character prefix), agent, or lease holder to keep.",
                True,
            ),
            "refile_as": (
                "string",
                "Comma-separated new ids, one per definition NOT kept, in `show` order: "
                "each is re-added under its new id in the same transaction.",
                False,
            ),
        },
        "api": lambda repo, a, agent: _api().resolve(
            repo, a["id"], keep=a["keep"], refile_as=a.get("refile_as", "") or "", agent=agent
        ),
        "payload": ("id", "kept_definition", "kept_holder", "lost", "refiled", "released"),
    },
    "ddflow_unblock": {
        "description": (
            "Release a BLOCKED item -- and every blocked item beneath it -- back into "
            "the queue, so `next` can offer them again. The inverse of ddflow_block, and "
            "how deferred work, or a whole archived section an import landed as blocked, "
            "becomes work once the OPERATOR says so: pass a phase id to release its "
            "section. Do not release held work on your own judgement. Returns "
            "nothing-to-do (exit 2) when nothing there is blocked."
        ),
        "properties": {
            "id": ("string", "Item id.", True),
            "note": ("string", "Why it is work again (who decided, and when).", False),
        },
        "api": lambda repo, a, agent: _api().unblock(
            repo, a["id"], note=a.get("note", "") or "", agent=agent
        ),
        "payload": ("id", "was", "released"),
    },
    "ddflow_session_start": {
        "description": "Open a session for provenance logging. Returns the session id.",
        "properties": {
            "model": ("string", "Your model id.", False),
            "tool": ("string", "Your harness, e.g. 'claude-code'.", False),
        },
        "api": lambda repo, a, agent: _api().session_start(
            repo, model=a.get("model", "") or "", tool=a.get("tool", "") or "", agent=agent
        ),
        "payload": ("session",),
    },
    "ddflow_rule_add": {
        "description": (
            "Add a project rule; duplicate-checked like every add (answer new | extends:ID | duplicate_of:ID | related:ID)."
        ),
        "properties": {
            "id": ("string", "Rule id, e.g. r-naming.", True),
            "title": ("string", "One-line rule statement.", True),
            "content": ("string", "Rule text (may be empty for check_only).", False),
            "tags": ("string", "Comma-separated tags.", False),
            "scope": ("string", "project (default) | phase | task | global.", False),
            "priority": ("integer", "Priority 0-100 (default 50).", False),
            "globs": ("string", "Comma-separated globs it governs.", False),
            "new": ("boolean", "Dedup answer: a different record.", False),
            "extends": ("string", "Dedup answer: extends record ID.", False),
            "duplicate_of": (
                "string",
                "Dedup answer: same as record ID.",
                False,
            ),
            "related": ("string", "Dedup answer: related to ID.", False),
            "check": (
                "boolean",
                "Dry run: list duplicates only.",
                False,
            ),
        },
        "api": lambda repo, a, agent: (
            _api().rule_dedup_check_dry_run(
                repo,
                a.get("content", "") or "",
            )
            if bool(a.get("check"))
            else _api().rule_add(
                repo,
                _api().Rule(
                    id=a["id"],
                    title=a["title"],
                    content=a.get("content", "") or "",
                    tags=_list_or_none(a, "tags") or [],
                    scope=a.get("scope", "project") or "project",
                    priority=int(a.get("priority", 50) or 50),
                    globs=_list_or_none(a, "globs") or [],
                ),
                agent=agent,
                check_dedup=True,
                dedup_answer=(
                    _api().RuleDedupAnswer("new", "")
                    if bool(a.get("new"))
                    else (
                        _api().RuleDedupAnswer("extends", a["extends"])
                        if a.get("extends")
                        else (
                            _api().RuleDedupAnswer("duplicate_of", a["duplicate_of"])
                            if a.get("duplicate_of")
                            else (
                                _api().RuleDedupAnswer("related", a["related"])
                                if a.get("related")
                                else None
                            )
                        )
                    )
                ),
            )
        ),
        "payload": ("id", "candidates"),
    },
    "ddflow_rule_list": {
        "description": (
            "List the project's rules, filtered by tag or scope: what governs the current work."
        ),
        "properties": {
            "tag": ("string", "Filter by this tag.", False),
            "scope": ("string", "Filter by this scope.", False),
            "limit": ("integer", "Maximum results (default 100).", False),
            "json": ("boolean", "JSON array.", False),
        },
        "api": lambda repo, a, agent: _api().rule_list(
            repo,
            tag=a.get("tag") or None,
            scope=a.get("scope") or None,
        ),
        "payload": ("rows", "count"),
    },
    "ddflow_rule_search": {
        "description": (
            "Search rules by title or content, ranked by relevance, for an area or topic."
        ),
        "properties": {
            "query": ("string", "Search query (keywords or regex).", True),
            "exact": ("boolean", "Exact phrase.", False),
            "regex": ("boolean", "Query is a regex.", False),
            "tag": ("string", "Filter results by this tag.", False),
            "scope": ("string", "Filter results by this scope.", False),
            "limit": ("integer", "Maximum results to return (default 10).", False),
        },
        "api": lambda repo, a, agent: _api().rule_search(
            repo,
            a["query"],
            limit=int(a.get("limit", 10) or 10),
            exact=bool(a.get("exact")),
            regex=bool(a.get("regex")),
            tag=a.get("tag") or None,
            scope=a.get("scope") or None,
        ),
        "payload": ("rows", "count", "query"),
    },
    "ddflow_rule_edit": {
        "description": (
            "Change fields of an existing rule; omitted fields stay. Recorded in the manifest."
        ),
        "properties": {
            "id": ("string", "Rule id to edit.", True),
            "title": ("string", "New title.", False),
            "content": ("string", "New content.", False),
            "tags": ("string", "Comma-separated tags.", False),
            "scope": ("string", "New scope.", False),
            "priority": ("integer", "New priority.", False),
            "globs": ("string", "Comma-separated globs.", False),
        },
        "api": lambda repo, a, agent: _api().rule_update(
            repo,
            a["id"],
            **(
                {
                    "title": a["title"],
                }
                if a.get("title")
                else {}
            ),
            **(
                {
                    "content": a["content"],
                }
                if a.get("content")
                else {}
            ),
            **(
                {
                    "tags": _list_or_none(a, "tags") or [],
                }
                if "tags" in a
                else {}
            ),
            **(
                {
                    "scope": a["scope"],
                }
                if a.get("scope")
                else {}
            ),
            **(
                {
                    "priority": int(a.get("priority") or 50),
                }
                if a.get("priority") is not None
                else {}
            ),
            **(
                {
                    "globs": _list_or_none(a, "globs") or [],
                }
                if "globs" in a
                else {}
            ),
        ),
        "payload": ("id",),
    },
    "ddflow_rule_remove": {
        "description": ("Delete a rule from the manifest; the removal is logged as an event."),
        "properties": {
            "id": ("string", "Rule id to remove.", True),
            "reason": ("string", "Why it is removed (recorded).", False),
        },
        "api": lambda repo, a, agent: _api().rule_remove(
            repo,
            a["id"],
        ),
        "payload": ("id",),
    },
    "ddflow_rule_show": {
        "description": (
            "One rule with all its metadata: title, content, tags, scope, priority, globs, timestamps."
        ),
        "properties": {
            "id": ("string", "Rule id to retrieve.", True),
        },
        "api": lambda repo, a, agent: _api().rule_get(
            repo,
            a["id"],
        ),
        "payload": (
            "id",
            "title",
            "content",
            "tags",
            "scope",
            "priority",
            "globs",
            "created",
            "updated",
        ),
    },
}


def _opt(flag: str, args: dict[str, Any], key: str | None = None) -> list[str]:
    """Build `--flag value`, or nothing when the caller did not supply one.

    There used to be a ``clearable`` parameter here, distinguishing "not supplied" from
    "supplied as empty" — a distinction argv erases and this had to rebuild, because
    over MCP `ddflow_update(id="X", needs="")` silently did nothing while
    `ddflow update X --needs ""` cleared the field. `ddflow_update` was its only caller,
    and that tool now goes through the typed `api` path, where `None` and `[]` are
    simply different values and no flag is needed.

    So it went, rather than staying as a parameter nothing passes: a branch no test can
    execute cannot be caught drifting, which is the reason its last caller was removed
    in the first place.
    """
    k = key or flag.lstrip("-").replace("-", "_")
    v = args.get(k)
    return [flag, str(v)] if v not in (None, "", []) else []


#: The per-CALL identity override every tool accepts except `ddflow_identify` itself.
#:
#: `ddflow_identify` declares identity per CONNECTION, which covers several agents that
#: each spawn their own server. It does not cover the configuration Claude Code actually
#: runs: subagents dispatched by one session share that session's MCP connection, so a
#: subagent that called `ddflow_identify` would re-identify its PARENT and every sibling
#: mid-flight. Their claims then share one holder -- and the glob-conflict check skips a
#: holder's own leases (`leases.acquire`), so two subagents claiming overlapping files
#: are both granted. The CLI has always had the per-call form (`--agent`); this is the
#: same thing on the other surface.
#: The skew-guard override (decision D-upgrade-skew-guard): the REASON an agent was told by the
#: operator to let this older ddflow write anyway. Accepted by every tool, stripped before the
#: tool sees its arguments, and deliberately NOT in any schema -- 90 copies of it would cost
#: more of every session's context than the rare refusal it answers; the refusal message and
#: the connection instructions name it where it is needed.
ALLOW_OLDER = "allow_older_version"
AS_AGENT = "as_agent"
_AS_AGENT_SPEC = (
    "string",
    "A subagent's own name, this call only.",
    False,
)


def _properties(spec: dict[str, Any]) -> dict[str, tuple[str, str, bool]]:
    """A tool's declared properties plus `as_agent`, which every tool but `identify`
    takes. One function, so the schema a client sees and the argument check the
    dispatcher applies can never disagree about it."""
    props = dict(spec["properties"])
    if not spec.get("identify"):
        props[AS_AGENT] = _AS_AGENT_SPEC
    return props


def _schema(spec: dict[str, Any]) -> dict[str, Any]:
    all_props = _properties(spec)
    props = {
        name: {
            "type": t,
            "description": desc,
            # An array is always of strings here; a schema without `items` is refused by
            # some clients' validators.
            **({"items": {"type": "string"}} if t == "array" else {}),
        }
        for name, (t, desc, _req) in all_props.items()
    }
    required = [n for n, (_t, _d, req) in all_props.items() if req]
    return {
        "type": "object",
        "properties": props,
        **({"required": required} if required else {}),
        "additionalProperties": False,
    }


#: The add tools: each takes the answer to the duplicate check (`api/_dedupe.py`).
ADD_TOOLS = (
    "ddflow_phase_add",
    "ddflow_task_add",
    "ddflow_bug_found",
    "ddflow_lesson_add",
    "ddflow_decision_add",
    "ddflow_research_add",
    "ddflow_memory_add",
)

DEDUPE_PROPERTIES: dict[str, tuple[str, str, bool]] = {
    "relation": (
        "string",
        "Answer to a 'possible duplicate' refusal: new | extends:ID | duplicate_of:ID | "
        "related:ID (the refusal lists candidates and options). Omit at first.",
        False,
    ),
    "check_only": (
        "boolean",
        "Dry run: write nothing, return the `candidates` this add would be refused for.",
        False,
    ),
}

for _name in ADD_TOOLS:
    TOOLS[_name]["properties"].update(DEDUPE_PROPERTIES)


# -- tool tiers -------------------------------------------------------------------------
#
# `[mcp].tools = core | standard | all` (D-lean-and-trusted (4)) decides which tools
# `tools/list` ADVERTISES. It is a start-time knob and nothing more: `TOOLS` stays the one
# registry, every tool stays callable by name whatever the tier, and `listChanged` stays
# false. A client that loads every schema up front pays for what is advertised, so the tier
# is a context-cost choice and never a permission.
#
# ONE data structure, three disjoint sets that together cover `TOOLS` exactly. A new tool
# therefore has to be placed (`tests/test_mcp_tool_tiers.py` fails until it is), rather
# than silently landing in whichever tier nobody thought about. Measured on this tree: core
# 39 KB, standard 67 KB, all 92 KB of compact `tools/list`.

#: The daily loop: pick work, claim it, satisfy gates, record what you learn, land it.
#: `setup` is here because the handshake of an unadopted repository tells the agent to call
#: it before anything else.
CORE_TOOLS = frozenset(
    "ddflow_" + n
    for n in (
        "brief next claim heartbeat gate_status gate_run gate_record gate_skip gate_verify "
        "complete merge status show recall bug_found bug_fixed bug_invalid lesson_add "
        "decision_add session_start session_end session_note session_prompt identify "
        "task_add update wait review help pr_sync similar setup"
    ).split()
)

#: Commonly used beyond the loop: reading the queue and memory, reshaping work, config.
STANDARD_EXTRA_TOOLS = frozenset(
    "ddflow_" + n
    for n in (
        "abandon block unblock release board progress doctor recover configure companions "
        "decision_applicable decision_list decision_show decision_supersede lesson_search "
        "flow_show pr_status pr_threads reviewers_list version_show research_add phase_add split resolve "
        "remove tests review_triage memory_add memory_list history cleanup render list "
        "rule_add rule_list rule_search rule_edit rule_remove rule_show workflow_state verify ci onboard "
        "dupes link"
    ).split()
)

#: Administration and rarely used capabilities: only in `all`.
FULL_ONLY_TOOLS = frozenset(
    "ddflow_" + n
    for n in (
        "external_sync import import_verify job_add job_end job_list job_run promote_add promote_deployed "
        "promote_status workflow workflow_drop workflow_gate workflow_pipeline flow_choose "
        "version_cut rebuild replay hooks precommit companions_add companions_verify bisect prompts pins loops cadence "
        "reviewers_detect memory_forget lesson_verify export bug_file_tasks"
    ).split()
)

TIERS = ("core", "standard", "all")
DEFAULT_TIER = "all"


def tier_tools(tier: str) -> frozenset[str]:
    """The tools `tools/list` advertises at `tier`; an unknown tier advertises everything."""
    if tier == "core":
        return CORE_TOOLS
    if tier == "standard":
        return CORE_TOOLS | STANDARD_EXTRA_TOOLS
    return frozenset(TOOLS)


def resolve_tier(repo: Path) -> str:
    """`[mcp].tools` for this repository (env `DDFLOW_MCP_TOOLS` wins), read once.

    Forward-compatible by construction: a value this version does not know (a newer
    release's tier in a shared config), or a config that cannot be read at all, advertises
    EVERYTHING rather than failing the handshake. Over-listing costs context; under-listing
    would hide a tool the agent needs.
    """
    try:
        from ..config import Config

        tier = Config.load(repo).mcp.tools
    except Exception:
        return DEFAULT_TIER
    return tier if tier in TIERS else DEFAULT_TIER


def tier_note(tier: str, *, names: bool = True) -> str:
    """What a tier hides and how to widen it. `ddflow_help` carries the names; the handshake
    (paid for on every session) only counts them and points at `ddflow_help`."""
    if tier not in TIERS or tier == "all":
        return ""
    hidden = sorted(set(TOOLS) - tier_tools(tier))
    wider = "`standard` or `all`" if tier == "core" else "`all`"
    head = (
        f"This connection lists the `{tier}` tool tier ({len(TOOLS) - len(hidden)} of "
        f"{len(TOOLS)} tools). The other {len(hidden)} are NOT in `tools/list` but are "
        f"still callable by name with the same arguments"
    )
    listed = f": {', '.join(hidden)}" if names else " (`ddflow_help` names them)"
    return (
        f"{head}{listed}. To list "
        f"them, set `[mcp].tools` to {wider} in .ddflow/config.toml (or "
        f"`DDFLOW_MCP_TOOLS`) and restart the server; the tier is read once at start."
    )


def _answer(a: dict[str, Any]):
    """The duplicate-check answer an add call carries: ``relation`` and ``check_only``."""
    from ..api._dedupe import Answer

    given = Answer.parse(str(a.get("relation", "") or ""))
    return Answer(given.relation, given.target, bool(a.get("check_only")))


def _configure_reported(repo, a, agent):
    """`ddflow_configure`, telling the operator when it changed the review budget."""
    from ..api.setup import report_budget_change

    edit = _api().ConfigEdit(
        set=a.get("set", "") or "",
        value=a.get("value", "") or "",
        append_toml=a.get("toml", "") or "",
        filter=a.get("filter", "") or "",
        explain=True,
        local=bool(a.get("local")),
    )
    return report_budget_change(repo, edit, _api().configure(repo, edit, agent=agent), agent=agent)


def _bisect(repo, a):
    """`ddflow_bisect`. `glob`, `repeat` and `max_runs` are CLI-only: the tools/list byte budget."""
    from ..api import bisect as B

    return B.bisect(
        repo,
        a.get("victim", "") or "",
        a.get("cmd", "") or "",
        candidates=a.get("candidates", "") or "",
        timeout_s=float(a.get("timeout") or 600),
    )


def _reopening(a: dict[str, Any]) -> bool:
    """`ddflow_bug_invalid`'s mode, for its `api` and its `payload` alike. Strict: the
    two modes do opposite things, so a `"false"` string must not pick one."""
    mode = a.get("reopen", False)
    if not isinstance(mode, bool):
        raise ValueError("reopen must be true or false")
    return mode


def _bug_reopen(repo, a: dict[str, Any], *, agent: str):
    """`bug reopen` (B7bdcc6b212), served by `ddflow_bug_invalid` with `reopen`. Evidence
    a reopen cannot record is refused (exit 3), not dropped; an empty one carries nothing."""
    from ..api.bug_reopen import bug_reopen
    from ..core import outcome as O

    if a.get("evidence"):
        return O.refused(
            "bug.reopened",
            "evidence is for closing a bug as invalid; a reopen records only its reason.",
            id=a["id"],
        )
    return bug_reopen(repo, a["id"], reason=a.get("reason", "") or "", agent=agent)


def _api():
    """Imported lazily: `surfaces` may reach `api`, and doing it at call time keeps the
    module import graph flat for anything that only wants the tool table."""
    from .. import api

    return api


def _regression_tests(args: dict[str, Any]) -> str | list[str]:
    """`regression_test` (a string -- or a list, from a client that sent one anyway) and
    `regression_tests` as one value for `api.bug_fixed`, which does the splitting.

    Not `_list_or_none`: its plain comma split cuts a parametrize id like `t[1,2]`. A
    lone string stays a string, so it is recorded verbatim as before (B227585c781).
    """
    single, many = args.get("regression_test") or "", args.get("regression_tests") or []
    if isinstance(single, str) and not many:
        return single
    as_list = ([single] if single else []) if isinstance(single, str) else list(single)
    return [*as_list, *([many] if isinstance(many, str) else many)]


def _list_or_none(args: dict[str, Any], key: str) -> list[str] | None:
    """`None` when absent, a list when supplied — INCLUDING the empty one.

    The whole point of the typed path. `""` supplied deliberately means "clear this
    field", and over argv that was indistinguishable from not supplying it at all: over
    MCP a dependency could be added and never removed, which is exactly the operation
    the loop detector tells you to perform.
    """
    if key not in args or args[key] is None:
        return None
    from ..config import csv_list

    return csv_list(args[key]) if isinstance(args[key], str) else list(args[key])


def _refusal_body(out: Any, payload_key: Any, body: Any) -> Any:
    """The JSON body of a call that did not do what was asked, led by WHY.

    A refused call used to return the tool's SUCCESS projection: `claim` refused for an
    overlap answered `{"item": null, "holder": null, "worktree": null, ...}` with the
    reason only in a second block, and the data the refusal did carry (the alternatives
    it names, the id) was projected away. An agent reading the first block -- which is
    what the wire contract tells a machine to read -- saw a broken success (B9cf58aaeaa).

    So a JSON body leads with a `refusal` object (reason first, then `outcome` and
    `exit`) on every REFUSAL (exit 3), and on any other non-zero exit whose declared shape
    the operation did not fill -- the null-padded success schema is the bug, whatever
    the code. The lead is named `refusal` for the call that "did not do what was asked",
    not for exit 3 alone: `refusal.exit` and `refusal.outcome` say which (`failed`,
    `nothing` or `refused`), and `_meta.exit` carries the same code. After the lead come
    the declared fields the operation set and every other wire field of its data -- on
    exit 1, 2 and 3 alike (claim's `alternatives`, later duplicate candidates). A field it
    never set is not invented as null on exit 1 and 3. Exit 2 keeps ALL the declared keys
    (null where unset), in the order the tool declares them, because "nothing" is still that
    tool's answer. (An operation that sets every declared key at exit 2 -- `heartbeat` with
    no lease, `workflow_drop` of a gate in neither pipeline -- fills its shape and is left
    alone, byte-identical to `--json`; the padded case is one that sets fewer.)

    A tool whose body is ONE data field (`ddflow_decision_show` answers its `decision`) is
    a null or scalar when that field is unset; a non-zero exit then still leads with the
    refusal, followed by the rest of the data (`id`), not the bare `null`.

    An exit 1 or 2 body that fills its declared shape is a RESULT and is left alone,
    byte-identical to the CLI's `--json`: `gate verify` fails WITH its results, `next` on
    an empty queue and `companions` with gaps are answers. So is an array at any exit
    (`loops` exits 1 with its findings). Their reason is the second content block.

    `refusal` is a name no operation's data uses; `outcome` and `reason` are both wire
    fields of some tools (`gate record`, `gate verify`) and could not lead unambiguously.
    Should a data field ever be named `refusal`, it is kept, as `refusal_data` (or that
    name with `_` appended while it is taken), never dropped.
    """
    if out.exit == 0 or isinstance(body, list):
        return body
    from ..core.outcome import EXIT_NAMES, NOTHING, REFUSED

    data = {k: v for k, v in out.data.items() if not k.startswith("_")}
    scalar = not isinstance(body, dict)
    projected = isinstance(payload_key, tuple)
    padded = projected and any(k not in data for k in payload_key)
    if not scalar and out.exit != REFUSED and not padded:
        return body
    lead: dict[str, Any] = {
        "refusal": {
            "reason": out.reason,
            "outcome": EXIT_NAMES.get(out.exit, str(out.exit)),
            "exit": out.exit,
        }
    }
    if scalar:
        # A string payload key names the one field the body was cut down to; unset, it is
        # the null that used to lead. What the operation did say still rides along.
        said = {k: v for k, v in data.items() if k != payload_key or v is not None}
    else:
        said = dict(body)
        if projected:
            # Declared keys the operation never set stay out (not invented as null) on
            # exit 1 and 3; exit 2 keeps its declared keys. Everything else it said is added.
            if out.exit != NOTHING:
                said = {k: v for k, v in said.items() if k in data}
            said.update({k: v for k, v in data.items() if k not in said})
    if "refusal" in said:
        key = "refusal_data"
        while key in said:  # never overwrite a field the operation also has
            key += "_"
        said[key] = said.pop("refusal")
    return {**lead, **said}


def _bounds() -> dict[str, Any]:
    from .mcp_bound import BOUNDS

    return BOUNDS


def _outcome_result(
    out: Any,
    payload_key: str | tuple[str, ...] = "",
    *,
    as_text: bool = False,
    bound: Any = None,
    args: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """An `Outcome` as an MCP tool result: JSON body, `isError` only for a real failure.

    Exit 2 ("nothing to do") and 3 ("coordination refused") are RESULTS the model must
    read and act on, exactly as on the string path. Only 1 is a failure. The reason,
    when there is one, leads the body: a caller that reads the first line has the
    actionable part, which is what the spec means by feedback a model can self-correct
    from.
    """
    # `payload_key` preserves a tool's EXISTING wire shape across migration. Moving
    # `ddflow_loops` to the typed path silently changed its body from a JSON array of
    # findings to an object wrapping them, breaking every consumer that iterated it --
    # two demo scenarios did. B37 exists to remove a duplicated rendering, not to
    # redefine contracts, and a migration that changes the wire format is worse than no
    # migration: the duplication was at least honest about what it returned.
    # `Outcome.body` is the ONE implementation of that projection, shared with the
    # CLI's `--json` -- which is the point, since the property being preserved is that
    # the two are the same parsed body. (MCP encodes it compact, the CLI indented, so
    # "byte-identical" is the parsed value, not the text; five reads are also cut
    # on MCP -- `mcp_bound`.)
    #
    # `as_text` covers the tools whose body is PROSE and always has been: `board` is
    # markdown, `doctor` is a report an operator reads, `replay` is a reconstruction
    # document. Their argv form carries no `--json`, so the string path returned the
    # human rendering -- and JSON-encoding it during migration would hand every existing
    # consumer one quoted string with `\n` in it instead of the document they parse.
    # The rendering itself lives in `views/`, below both surfaces, so this is a choice of
    # ENCODING here and not a second renderer.
    if not as_text and (out.data.get("extended") or out.data.get("check_only")):
        # An add that went onto an existing record, or a dry run: what the check decided
        # IS the answer, and the tool's usual `{"id": ...}` projection would drop it.
        payload_key = ""
    if isinstance(payload_key, tuple):
        # merge / complete carry their optional extras as the CLI's --json does: what the
        # document refresh did (B-export-refresh), the base's health after a merge
        # ([ci].on_merge) and the progress block after a completion. Absent when nothing
        # was produced, so the shape is unchanged.
        payload_key = (
            *payload_key,
            *(k for k in _OPTIONAL_KEYS if k in out.data and k not in payload_key),
        )
    if as_text:
        body = out.body(payload_key)
        if not isinstance(body, str):
            raise TypeError(
                f"{out.kind}: declared `text` but its body is a {type(body).__name__}. "
                f"A text tool's payload must name a rendered string."
            )
        # The reason is NOT prepended to a document. `doctor` ends with "2 problem(s)."
        # and the string path returned the report alone, so prefixing it both duplicates
        # the summary and changes a body consumers already parse. A document's renderer
        # decides its own lead; that is what makes it a document.
        #
        # Unless it is EMPTY — then the reason is all there is, and returning nothing for
        # a failed call is the unavailable-as-success class with no text to hide behind.
        if not body.strip() and out.reason:
            body = out.reason
        return _text(body, error=(out.exit == 1), meta={"exit": out.exit})

    full = _refusal_body(out, payload_key, out.body(payload_key))
    note = None
    if bound is not None:
        # The bounded reads (`mcp_bound`): the full body, cut and said so. The CLI's
        # `--json` is the whole body, and the parsed MCP body equals it except here.
        full, note = bound(full, args or {})
    # Compact: a model reads every byte of this and indentation is a quarter of it.
    body = json.dumps(full, separators=(",", ":"), default=str)
    result = _text(body, error=(out.exit == 1), meta={"exit": out.exit})
    if out.reason:
        # A SECOND content block, never a prefix. The reason used to be prepended to the
        # JSON, which reads well and breaks every machine consumer: `json.loads` on
        # `content[0].text` fails at character 0. `demos/harness.py::jtool` does exactly
        # that, and three of the six demo scenarios broke silently during the B37
        # migration — for every tool whose outcome is exit 2 or 3, which is most of the
        # read-only ones on a fresh project.
        #
        # The wire-shape test did not catch it because it skipped to the first `{` or `[`
        # before parsing. Its own docstring warns about precisely that kind of
        # accommodation ("validated the contents while accommodating the exact shape
        # change it was written to prevent") and it had one anyway.
        #
        # Both readers are served: a machine indexes `content[0]`, and a model is shown
        # every block, so the reason still reaches the thing that has to act on it. A
        # refusal's JSON body ALSO leads with it (`_refusal_body`): the block a machine
        # reads must not be the success shape in nulls (B9cf58aaeaa). The prose block
        # stays, for the array bodies that cannot carry it and for a model reading text.
        # `_meta.exit` carries the code either way.
        result["content"].append({"type": "text", "text": out.reason})
    if note:
        # After the reason, so the reason keeps the position documented above; the
        # bounded read's statement of what it cut is the block after it.
        result["content"].append({"type": "text", "text": note})
    return result


#: What a declared agent name may contain. It becomes a log SHARD FILENAME, so a name
#: with a path separator would write outside the events directory, and one with a
#: newline would corrupt the line-oriented log. Refused at declaration time, where the
#: caller can read why, rather than at the first write.
#:
#: `fullmatch`, and no anchors. With `^...$` and `.match()` this accepted
#: `"reviewer\n"` — Python's `$` matches at end-of-string OR immediately before a final
#: newline — so the pattern did not refuse the one character the comment above singles
#: out. It was unreachable in practice only because the handler strips the name first,
#: which means the guarantee lived in an incidental `.strip()` rather than in the check
#: credited with it. Two lines that disagree about which one is load-bearing is how the
#: next edit removes the wrong one.
_VALID_AGENT = re.compile(r"[A-Za-z0-9._-]{1,64}")


def _default_agent(repo: Path) -> tuple[str, str]:
    """(identity, where it came from) for a connection that declared none.

    The SOURCE matters as much as the name. "you are `alpha`, from DDFLOW_AGENT" and
    "you are `alpha`, because that is this directory's name" call for different
    reactions: the first was set deliberately by whatever spawned you, the second is a
    guess that every sibling in this tree will make identically.
    """
    from ..config import Config

    # The EFFECTIVE default, not the tree-derived one. Reporting the tree name while
    # `DDFLOW_AGENT` was set made `ddflow_identify` misreport the single thing it
    # exists to make visible.
    from ..infra.log import resolve_agent_id

    try:
        cfg = Config.load(repo)
    except Exception:
        cfg = None
    who, layer = resolve_agent_id(repo, cfg)
    return who, {
        "env": "from DDFLOW_AGENT",
        "config": "from [agent].id in config",
        "derived": "derived from the working tree",
        "explicit": "declared",
    }[layer]


class Server:
    """One connection. Which, deliberately, is not the same thing as one agent.

    Identity is how every attribution in the log works -- who holds a lease, who ran a
    gate, whether the reviewer was a different agent than the author. The default is
    derived from the working tree (`EventLog.default_agent_id`), and that is right for
    the ordinary case of one agent per worktree.

    It is WRONG, silently, for the case this tool exists to support: several agents or
    subagents working the same tree at once. Each spawns its own stdio server, every
    one of them resolves the same cwd to the same identity, and their events merge into
    one indistinguishable stream. Nothing errors. `brief` then reports another agent's
    item as "what you were doing", and reviewer-independence compares an agent with
    itself and is satisfied.

    There is no signal that can distinguish them -- so identity has to be DECLARED:
    `DDFLOW_AGENT` in the environment, or `ddflow_identify` on the connection, or an
    `as_agent` argument on the individual call. Explicit beats derived, innermost wins.
    """

    def __init__(self, repo: Path, agent: str = "", *, called_from: Path | None = None) -> None:
        self.repo = Path(repo)
        #: WHERE THE CALLER IS, unresolved. `self.repo` is the primary checkout -- that
        #: is what makes every worktree share one event log -- and resolving to it threw
        #: away the fact `claim` needs: whether the caller was already inside a worktree.
        #: Worktree ADOPTION was therefore unreachable from MCP, which is the surface the
        #: harnesses it was written for (Claude Code, Cursor) actually drive.
        self.called_from = Path(called_from) if called_from else self.repo
        self.protocol = SUPPORTED_PROTOCOLS[0]
        #: Declared identity for this connection; empty means "use the process
        #: default", which is the backward-compatible single-agent behaviour.
        self.agent = agent
        if not agent:
            # A server restarted under the same harness keeps what the agent declared
            # there, or its shell (which still reads the record) and it would split.
            from ..infra import harness_identity

            self.agent = harness_identity.own(self.repo)
        #: Which tools `tools/list` advertises: `[mcp].tools`, read ONCE here. Start-time
        #: only -- `listChanged` is false and there is no call that widens it.
        self.tier = resolve_tier(self.repo)
        #: What the client called itself at `initialize`. A LABEL, never an identity:
        #: every subagent of one harness reports the same `clientInfo.name`, so using
        #: it as an id would reproduce the exact collapse above while looking specific.
        self.client_info: dict[str, Any] = {}
        #: Re-instruction cadence, per CONNECTION. `initialize` delivers the rules once and
        #: nothing re-states them afterwards; after a context compaction the model may
        #: retain none of it, and MCP has no server->client context-injection primitive. A
        #: footer on tool results is the only channel that survives, so these two counters
        #: decide how often it is allowed to speak. Per-connection because that is the
        #: lifetime of the context it is compensating for.
        self._calls_since_footer = 0
        self._last_footer_at = 0.0
        #: Writes one JSON-RPC frame to the client; `serve` sets it. Without it (a bare
        #: `handle` call) a long tool simply sends no progress.
        self.notify: Callable[[dict[str, Any]], None] | None = None

    def _progress(self, params: dict[str, Any]) -> Callable[[str], None] | None:
        """A per-line `notifications/progress` sender for this call, or None.

        A client that sent `_meta.progressToken` resets its idle timer on each one; a
        review of 2055 s was aborted at 1800 s with none (bug B206)."""
        meta = params.get("_meta")
        token = meta.get("progressToken") if isinstance(meta, dict) else None
        send = self.notify
        if token is None or send is None:
            return None
        count = [0]

        def say(line: str) -> None:
            count[0] += 1
            try:
                send(
                    {
                        "jsonrpc": "2.0",
                        "method": "notifications/progress",
                        "params": {
                            "progressToken": token,
                            "progress": count[0],
                            "message": line.strip()[:500],
                        },
                    }
                )
            except Exception:  # a closed pipe must not fail the review
                pass

        return say

    def _someone_else(self, per_call: str) -> bool:
        """Does a per-call `as_agent` name an agent OTHER than this connection's own?

        The connection's own identity is the declared one, else the derived default --
        the same answer `ddflow_identify` reports. Naming it again per call is the same
        agent; naming anyone else is a subagent riding this connection.
        """
        if not per_call:
            return False
        own = self.agent or _default_agent(self.repo)[0]
        return per_call != own

    def _invoke(self, spec, args, agent, per_call, params):
        """Run a tool's typed `api`; the connection facts it needs are passed in."""
        # `called_from` only where the tool asks for it. `claim` is the one
        # operation whose behaviour depends on WHERE the caller is standing
        # rather than which repo it is in: an agent whose harness already put
        # it in a worktree should have that tree ADOPTED, and resolving to the
        # primary loses the only fact that says so. `main()` computed it and
        # discarded it, which is why adoption was unreachable from MCP.
        if spec.get("wants_called_from"):
            where = self.called_from
            if self._someone_else(per_call):
                # Where the connection stands is where the CONNECTION's
                # identity works, for EVERY tool that asks. A subagent sharing
                # it (Claude Code's do) is not standing in its parent's
                # harness tree: `claim` adopted that tree and branch
                # (B7c7a0d9222), and for an item claimed --no-worktree,
                # `gate_run` ran the parent's tree as the subagent's pass and
                # `merge` landed the parent's branch (B11e4c5a185). Asked from
                # the primary, the ITEM decides -- its own tree and branch,
                # else the "name the branch" answers -- as from the CLI.
                where = self.repo
            extra = {}
            if spec.get("wants_progress") and (say := self._progress(params)):
                extra["on_progress"] = say
            result = spec["api"](self.repo, args, agent, called_from=where, **extra)
        else:
            result = spec["api"](self.repo, args, agent)
        return result

    def _override_skew(self, reason: Any, agent: str) -> None:
        """Record the session-scoped skew override this call carries (`allow_older_version`)."""
        from ..config import Config
        from ..infra.log import EventLog, effective_agent_id

        if not isinstance(reason, str):
            raise ValueError("allow_older_version must be a string: the reason")
        cfg = Config.load(self.repo)
        log = EventLog(self.repo, effective_agent_id(self.repo, cfg, agent), log_cfg=cfg.log)
        log.override_skew(reason)

    def handle(self, msg: dict[str, Any]) -> dict[str, Any] | None:
        method = msg.get("method", "")
        mid = msg.get("id")
        if method == "initialize":
            params = msg.get("params") or {}
            want = params.get("protocolVersion", "")
            self.protocol = want if want in SUPPORTED_PROTOCOLS else SUPPORTED_PROTOCOLS[0]
            ci = params.get("clientInfo")
            self.client_info = dict(ci) if isinstance(ci, dict) else {}
            return _ok(
                mid,
                {
                    "protocolVersion": self.protocol,
                    "capabilities": {
                        "tools": {"listChanged": False},
                        "resources": {"listChanged": False},
                        # Prompts are how a client surfaces a workflow as a slash
                        # command. Omitting the capability means a spec-respecting
                        # client never calls prompts/list, so the commands exist and
                        # are unreachable — which is indistinguishable, from the
                        # operator's side, from not having written them.
                        "prompts": {"listChanged": False},
                    },
                    "serverInfo": SERVER_INFO,
                    "instructions": _instructions(self.repo, self.agent, self.tier),
                },
            )
        if method in ("notifications/initialized", "notifications/cancelled"):
            return None
        if method == "ping":
            return _ok(mid, {})
        if method == "tools/list":
            advertised = tier_tools(self.tier)
            return _ok(
                mid,
                {
                    "tools": [
                        {"name": n, "description": s["description"], "inputSchema": _schema(s)}
                        for n, s in sorted(TOOLS.items())
                        if n in advertised
                    ]
                },
            )
        if method == "tools/call":
            params = msg.get("params") or {}
            name = params.get("name", "")
            args = params.get("arguments") or {}
            allow_older: Any = None
            if ALLOW_OLDER in args:
                args = dict(args)
                allow_older = args.pop(ALLOW_OLDER)
            spec = TOOLS.get(name)
            if spec is None:
                return _ok(
                    mid,
                    _text(
                        f"unknown tool {name!r}. Available: {', '.join(sorted(TOOLS))}", error=True
                    ),
                )
            missing = [
                n for n, (_t, _d, req) in spec["properties"].items() if req and not args.get(n)
            ]
            if missing:
                return _ok(
                    mid, _text(f"missing required argument(s): {', '.join(missing)}", error=True)
                )
            # An argument this tool does not have is an ERROR, not something to drop.
            # Every schema here declares `additionalProperties: false` and nothing
            # enforced it, so a caller passing `id="D1"` to a tool with no `id` got a
            # success and a decision under a generated id — then `supersedes: D1`
            # pointed at nothing. Silence at an API boundary is the silent-knob-drop
            # class, and an agent cannot see it at all: it has only the reply.
            known = _properties(spec)
            unknown = sorted(set(args) - set(known))
            if unknown:
                return _ok(
                    mid,
                    _text(
                        f"unknown argument(s) for {name}: {', '.join(unknown)}. "
                        f"Known: {', '.join(sorted(known))}",
                        error=True,
                    ),
                )
            # The per-call identity, stripped BEFORE the tool sees its arguments so no
            # api lambda has to know it exists. Validated with the same rule as a
            # declaration: it becomes a log shard filename either way.
            agent = self.agent
            per_call = ""  # the `as_agent` this call named, if any
            if AS_AGENT in args:
                args = dict(args)
                want = args.pop(AS_AGENT)
                if not isinstance(want, str):
                    return _ok(mid, _text(f"{AS_AGENT} must be a string", error=True))
                want = want.strip()
                if want and not _VALID_AGENT.fullmatch(want):
                    return _ok(
                        mid,
                        _text(
                            f"{want!r} is not a usable agent name: use letters, digits, "
                            f"'.', '_' or '-', up to 64 characters.",
                            error=True,
                        ),
                    )
                per_call = want
                agent = want or agent
            # The typed path, when this tool has one. No argv, no re-parsing, no
            # scraping stdout, and no swapping process-global streams -- which is what
            # made the string path non-reentrant. `api` is where a protocol adapter
            # belongs: above the domain, beside the other surface, not THROUGH it.
            if spec.get("identify"):
                want = args.get("agent", "")
                if not isinstance(want, str):
                    return _ok(mid, _text("agent must be a string", error=True))
                want = want.strip()
                # A name that is not usable as a log shard filename is refused HERE,
                # where the agent can read the reason and retry, rather than at the
                # first write -- by which point the caller believes it is identified.
                if want and not _VALID_AGENT.fullmatch(want):
                    return _ok(
                        mid,
                        _text(
                            f"{want!r} is not a usable agent name: use letters, digits, "
                            f"'.', '_' or '-', up to 64 characters.",
                            error=True,
                        ),
                    )
                self.agent = want
                # The same agent's shell commands take it too (Bfad021e8d9).
                from ..infra import harness_identity

                shell = harness_identity.declare(self.repo, want)
                if want:
                    detail = "declared on this connection" + (
                        f"; {shell}: pass --agent to the CLI" if shell else ", and for its shell"
                    )
                else:
                    want_who, detail = _default_agent(self.repo)
                    who = want_who
                    detail = f"not declared; {detail}"
                who = want or who
                note = ""
                if not want and detail.endswith("working tree"):
                    note = (
                        " Every agent in this tree derives the SAME name, so if you are "
                        "one of several here, declare one."
                    )
                if not want and shell:
                    note += f" Its shell may still use the previous name ({shell})."
                return _ok(
                    mid,
                    _text(
                        f"identified as {who!r} ({detail}). Claims, gate outcomes and "
                        f"reviews on this connection are attributed to it.{note}"
                    ),
                )
            if "api" in spec:
                try:
                    if allow_older is not None:
                        self._override_skew(allow_older, agent)
                    result = self._invoke(spec, args, agent, per_call, params)
                except SkewRefused as exc:
                    # Exit 3, like every refusal: a result to act on, not a failed call.
                    return _ok(mid, _text(str(exc), meta={"exit": 3}))
                except (KeyError, TypeError, ValueError) as exc:
                    return _ok(mid, _text(f"bad arguments: {exc}", error=True))
                # `text` may be a bool or a predicate on the arguments: `render`
                # returns a document with `--show` and a file list without it, and which
                # it is cannot be known until the call.
                # Both may be a value or a predicate on the arguments: `render` returns
                # a document with `--show` and a file list without it, and which it is
                # cannot be known until the call. Resolved together so the two can never
                # disagree -- a text encoding over a tuple payload is a TypeError.
                wants_text = spec.get("text", False)
                payload = spec.get("payload", "")
                if callable(wants_text):
                    wants_text = wants_text(args)
                if callable(payload):
                    payload = payload(args)
                out = _outcome_result(
                    result,
                    payload,
                    as_text=bool(wants_text),
                    bound=_bounds().get(name),
                    args=args,
                )
                # The footer goes on LAST, after the reason block, so it never comes between
                # a caller and the answer it asked for — `content[0]` is still the body and
                # `jtool`-style consumers are untouched.
                if name == "ddflow_help" and (tiered := tier_note(self.tier)):
                    out["content"].append({"type": "text", "text": tiered})
                note = _obligation_footer(self)
                if note:
                    out["content"].append({"type": "text", "text": note})
                return _ok(mid, out)

            # No argv fallback. Every tool declares `api`, `ARGV_TOOLS_CEILING` is 0, and
            # `test_every_tool_has_exactly_one_dispatch_mechanism` requires exactly one
            # mechanism per tool — so a tool arriving here has NO dispatch, which is a
            # packaging fault rather than a caller error. Said plainly instead of falling
            # through to a path that no longer exists.
            return _ok(
                mid,
                _text(
                    f"{name} declares no dispatch mechanism. This is a ddflow bug, not a "
                    f"problem with the call.",
                    error=True,
                ),
            )
        if method == "resources/list":
            return _ok(
                mid,
                {
                    "resources": [
                        {
                            "uri": "ddflow://board",
                            "name": "Work queue",
                            "description": "The full queue with the critical path.",
                            "mimeType": "text/markdown",
                        },
                        {
                            "uri": "ddflow://brief",
                            "name": "Session brief",
                            "description": "Budgeted session-start pack.",
                            "mimeType": "text/markdown",
                        },
                        {
                            "uri": "ddflow://lessons",
                            "name": "Lessons",
                            "description": "Everything learned so far.",
                            "mimeType": "text/markdown",
                        },
                        {
                            "uri": "ddflow://lessons-summary",
                            "name": "Lessons summary",
                            "description": "Every live lesson in one paragraph, by tag.",
                            "mimeType": "text/markdown",
                        },
                        {
                            "uri": "ddflow://research",
                            "name": "Research log",
                            "description": "Findings with verdicts and probes.",
                            "mimeType": "text/markdown",
                        },
                    ]
                },
            )
        if method == "resources/read":
            uri = (msg.get("params") or {}).get("uri", "")
            # Every resource goes through the CLI, like every tool. The lessons and
            # research URIs used to fold the log directly — a second data path that
            # re-wired EventLog + fold without `Ctx`'s config and agent resolution, in
            # a module whose whole premise is "one implementation, two doors". The
            # table also carried a dead entry for `ddflow://lessons` that the branch
            # above it shadowed, which is how a second path hides: nothing reads the
            # line, so nothing contradicts it.
            cmd = {
                "ddflow://board": lambda repo: _api().board(repo).data["text"],
                # Through the API, like every tool. This served the resources by invoking
                # the CLI in-process and scraping its stdout, which was the last live user
                # of `_run_cli` and therefore the last reason the process-global stream
                # swap existed at all (B97).
                "ddflow://brief": lambda repo: _api().brief(repo).data["text"],
                "ddflow://lessons": lambda repo: _api().render(repo, show="lessons").data["text"],
                "ddflow://lessons-summary": lambda repo: (
                    _api().render(repo, show="lessons-summary").data["text"]
                ),
                "ddflow://research": lambda repo: _api().render(repo, show="research").data["text"],
            }.get(uri)
            if not cmd:
                return _err(mid, -32602, f"unknown resource {uri!r}")
            return _ok(
                mid,
                {"contents": [{"uri": uri, "mimeType": "text/markdown", "text": cmd(self.repo)}]},
            )
        if method == "prompts/list":
            from ..services import prompts as P

            return _ok(
                mid,
                {
                    "prompts": [
                        {
                            "name": name,
                            "title": title,
                            "description": desc,
                            "arguments": [
                                {
                                    "name": arg,
                                    "description": f"Optional: narrow the workflow to {arg}.",
                                    "required": False,
                                }
                                for arg in args
                            ],
                        }
                        # `all_commands`, not `COMMANDS`: operator-defined `[[macro]]`
                        # blocks are listed beside the shipped workflows, because a mode
                        # that has to be asked for by name is a mode nobody finds.
                        for name, (title, desc, args) in sorted(P.all_commands(self.repo).items())
                    ]
                },
            )
        if method == "prompts/get":
            from ..services import prompts as P

            params = msg.get("params") or {}
            name = params.get("name", "")
            args = params.get("arguments") or {}
            known = P.all_commands(self.repo)
            if name not in known:
                return _err(
                    mid,
                    -32602,
                    f"unknown prompt {name!r}. Known: {', '.join(sorted(known))}"
                    + P.not_loaded_note(self.repo),
                )
            try:
                if name in P.COMMANDS:
                    tmpl = P.resolve_command(name, self.repo)
                    # Every declared argument is bound, empty when absent: the renderer is
                    # strict about undefined names, and a command that raises because the
                    # operator omitted an optional argument is a command nobody uses twice.
                    declared = dict.fromkeys(P.COMMANDS[name][2], "")
                    text = P.render(
                        tmpl, **{**declared, **args, "test_gates": _test_gates(self.repo)}
                    )
                else:
                    # A MACRO. Its declared params are REQUIRED, unlike a shipped
                    # command's optional `scope`: an operator who declares a parameter is
                    # saying the mode does not make sense without it, and a prompt rendered
                    # with a hole in it reads as a complete instruction.
                    from ..services import macros as M

                    text = M.render(
                        M.load_macros(self.repo)[name],
                        self.repo,
                        {k: str(v) for k, v in args.items()},
                    )
            except (P.TemplateError, _macro_error()) as exc:
                return _err(mid, -32602, str(exc))
            return _ok(
                mid,
                {
                    "description": known[name][1],
                    "messages": [{"role": "user", "content": {"type": "text", "text": text}}],
                },
            )
        return _err(mid, -32601, f"method not found: {method}")


def _obligation_footer(server) -> str:
    """The re-instruction footer, or "" — see `services/obligations.py` and `[reinstruct]`.

    Two guards, in this order, because the cheap one comes first: the CADENCE is counters on
    the connection and costs nothing, and only once it opens does this fold the log. Both
    `every_calls` and `every_seconds` must be satisfied, so a burst of calls does not
    produce a burst of footers.

    Swallows everything. A footer is a courtesy on top of an answer the caller asked for,
    and a broken courtesy must never turn a successful tool call into a failure.
    """
    try:
        from ..config import Config
        from ..core.model import fold
        from ..infra.log import EventLog
        from ..services import obligations as OB

        cfg = Config.load(server.repo)
        if not cfg.reinstruct.enabled:
            return ""
        server._calls_since_footer += 1
        now = time.time()
        if server._calls_since_footer < cfg.reinstruct.every_calls:
            return ""
        if now - server._last_footer_at < cfg.reinstruct.every_seconds:
            return ""
        state = fold(EventLog(server.repo, log_cfg=cfg.log).read_all(), strict=False)
        text = OB.footer(state, cfg, repo=server.repo, limit=cfg.reinstruct.max_items)
        if not text:
            # Nothing to say. The counters are NOT reset: a quiet project should not have
            # to wait another twelve calls once something does come up.
            return ""
        server._calls_since_footer = 0
        server._last_footer_at = now
        return text
    except Exception:
        return ""


def _macro_error() -> type[Exception]:
    """`macros.MacroError`, fetched lazily so the tool table does not drag the service in."""
    from ..services.macros import MacroError

    return MacroError


def _test_gates(repo: Path) -> list[str]:
    """Every configured gate that looks like a test suite, beyond `unit_tests`.

    Read from the project's own config so the `all-tests` command names the suites
    that actually exist here, rather than a generic list the reader has to translate.
    """
    try:
        from ..config import Config
        from ..services.gates import load_gates

        cfg = Config.load(repo)
        gates = load_gates(repo, cfg)
    except Exception:
        return []
    return sorted(
        g.id
        for g in gates.values()
        if g.id != "unit_tests"
        and g.is_command_gate
        and any(w in g.id for w in ("test", "e2e", "smoke", "integration", "ui"))
    )


#: Keys a tool's payload carries only when the operation produced them.
_OPTIONAL_KEYS = ("export_refresh", "ci", "progress")


def _instruction_vars(repo: Path, agent: str = "") -> dict[str, Any]:
    """Everything `mcp_instructions.md` can render from.

    Gathered defensively: this runs inside the `initialize` handshake, which must
    succeed even in a repository that is broken, half-configured or not adopted at all.
    Every lookup that can fail contributes its own default rather than taking the whole
    handshake down, because a server that refuses to start cannot tell anyone why.

    It also must not WRITE anything — a handshake that adopts the repository is the bug
    this file already fixed once, and `Store` learned the same lesson separately.
    """
    adopted = (repo / ".ddflow" / "config.toml").is_file()
    v: dict[str, Any] = {
        "adopted": adopted,
        "setup_todo": [],
        "companions": [],
        "missing_companions": [],
        "unregistered_companions": [],
        "uninstalled_companions": [],
        # Seeded here, with every sibling, because the block that computes these sits
        # AFTER the `if not adopted: return v` below -- so an unadopted repository got
        # a variable set the template could not render, and the whole handshake became
        # "ddflow's instruction template could not be loaded". The template guards the
        # use (`{% if adopted %}`), but a guard is only as good as the engine's
        # willingness to short-circuit, and one of the two did not. Defaults do not
        # depend on which branch ran.
        # The rules surface: present, stripped, drifted or gone. Seeded with its siblings
        # so the unadopted path renders — the lesson B153 cost a broken handshake for every
        # first-time user.
        "rules_drift": [],
        "unchecked_companions": [],
        "actionable_companions": [],
        "gate_gaps": [],
        "recoverable": 0,
        "ready": 0,
        "running": 0,
        "blocked": 0,
        "open_bugs": 0,
        "loops": 0,
        "task_pipeline": [],
        # Set by `_instructions` from the connection's tier; seeded here so the template's
        # variable contract holds on every path.
        "tool_tier_note": "",
        "require_outcome": True,
        "importable": 0,
        "queue_is_empty": True,
        "imported_total": 0,
        "imported_no_globs": 0,
        "imported_shipped_drift": 0,
    }
    # Cheap enough for a handshake: `glob` on a handful of known paths, no parsing.
    # The point is only to know whether to OFFER the import, not to do it.
    try:
        from ..config import Config as _Cfg
        from ..services import importer as IM

        # The CONFIGURED sources: a project that moved its journal to a path the
        # defaults do not know was told "nothing to import" about its whole history.
        v["importable"] = len(IM._files(repo, IM.all_source_globs(_Cfg.load(repo))))
    except Exception:
        pass
    if not adopted:
        return v

    try:
        from ..config import Config

        cfg = Config.load(repo)
    except Exception:
        return v
    # Checked BEFORE the expensive blocks: two `read_text` calls, and it is the one fact
    # that decides whether the agent has any project rules at all.
    try:
        from ..services.adopt import rules_status

        v["rules_drift"] = [
            {"path": r.path, "state": r.state, "detail": r.render()}
            for r in rules_status(repo)
            if r.needs_attention
        ]
    except Exception:
        pass
    v["task_pipeline"] = list(cfg.gates.task_pipeline)
    v["require_outcome"] = bool(cfg.gates.require_outcome)

    # Each block is independent, and a failure in one must not cost the others: a
    # project with a bad reviewer block should still be told what is ready to work.
    try:
        from ..services.gates import load_gates

        ut = load_gates(repo, cfg).get("unit_tests")
        if not ut or not ut.command or "set [gate.unit_tests]" in ut.command:
            v["setup_todo"].append(
                "No test command is configured. Set it with `ddflow_configure`: "
                '`[gate.unit_tests]` / `command = "<your test command>"`. Until then '
                "the unit_tests gate reports UNAVAILABLE and cannot pass."
            )
    except Exception:
        pass
    try:
        from ..services.review import load_reviewers

        if not load_reviewers(repo):
            v["setup_todo"].append(
                "No cross-family reviewer is configured, so the `critic` gate cannot "
                "run and `ddflow_complete` will refuse. Call "
                "`ddflow_reviewers_detect` with write=true — it finds a local model "
                "server if one is running and records it in the git-ignored "
                ".ddflow/local/reviewers.toml, never the committed config."
            )
    except Exception:
        pass
    try:
        # `probe=False`: detection shells out, and the handshake is the one call an
        # agent waits on before it can do anything at all. Registration state is read
        # from config files and is free; whether the binary exists can wait for
        # `ddflow_companions`, which is what the instruction tells it to call.
        from ..services import companions as CO

        statuses = CO.scan(repo, probe=False)
        v["companions"] = [
            {
                "id": st.companion.id,
                "title": st.companion.title,
                "gates": list(st.companion.gates),
                # Pre-joined, because `trim_blocks` eats the newline after a block tag:
                # a nested `{% for %}` closed at the end of a content line takes that
                # line's newline with it, and every bullet lands on one line. Both
                # renderers agree on that, so it is the template's shape to avoid, not
                # an engine difference to work around.
                "gates_text": ", ".join(st.companion.gates) or "—",
                "state": st.state,
                "install": st.companion.install,
                "url": st.companion.url,
                "default": st.companion.default,
                # The CLI JSON payload carries this; omitting it here meant the
                # template could not tell a server from a command-line tool even if it
                # wanted to -- the same CLI/MCP divergence, inside the fix for it.
                "kind": st.companion.kind,
                "usable": st.usable,
                "is_gap": st.is_gap,
                "is_unknown": st.is_unknown,
                "advice": st.advice,
            }
            for st in statuses
        ]
        # Split by what the AGENT would have to DO about each, because the two need
        # different permission from the operator: wiring up a server that is already
        # on the machine is a config edit, while installing one runs an install
        # command. Reporting them as one list made the instruction vague where it
        # most needed to be specific.
        # `is_gap`, not `state != "registered"`. An installed `cli` companion can never
        # be "registered", so the old test put it in this list on every connection and
        # the template told the agent -- as "something to DO" -- to register it. The
        # agent obeys and gets a refusal, having been instructed by the server itself.
        # Three buckets, because there are three different things to DO about them,
        # and the template renders each separately. One list called
        # "missing_companions" made the instruction say "install and register these"
        # over a set that included a tool needing no registration and a tool nobody
        # had looked for.
        # Bucketed by ADVICE, not by state: with `probe=False` an mcp companion is
        # definitely not registered and its install state is unknown, which is neither
        # "one command away" nor "go install it". Splitting on `state` put it in no
        # bucket, so the handshake computed three lists and dropped the only non-empty
        # case on the floor.
        by = {
            a: [c for c in v["companions"] if c["advice"] == a and c["default"]]
            for a in ("register", "install", "check")
        }
        v["unregistered_companions"] = by["register"]
        v["uninstalled_companions"] = by["install"]
        v["unchecked_companions"] = by["check"]
        v["missing_companions"] = by["register"] + by["install"]
        # ONE list for the template, because the proposal an agent makes is the same in
        # all three cases -- tell the operator, give them the command, let them decide.
        # Only the CLAIM about install state differs, and that is what `state_word`
        # carries. Splitting them into three rendered blocks dropped the install
        # command from the commonest case and left the agent nothing to act on.
        by_id = {c["id"]: c for c in v["companions"]}
        v["actionable_companions"] = [
            {**by_id[st.companion.id], "state_word": word}
            for st, word in CO.actionable(statuses, grouped=True)
        ]
        cover = CO.gate_coverage(repo, statuses, v["task_pipeline"])
        # A gate is only a GAP if every companion that could serve it is known absent.
        # With `probe=False` the cli companions are all `None`, so a plain "no ids"
        # test reported `rules` as unserved on every connection even with the tool on
        # the PATH -- telling the agent a gate has nothing behind it on the strength of
        # not having checked.
        unknown_for: dict[str, bool] = {}
        for st in statuses:
            if st.usable is None:
                for g in st.companion.gates:
                    unknown_for[g] = True
        v["gate_gaps"] = [g for g, ids in cover.items() if not ids and not unknown_for.get(g)]
    except Exception as exc:
        # NOT `pass`. A broad catch here is right -- a malformed registry must not stop
        # the handshake, and an agent with no instructions is worse than one with
        # partial ones -- but swallowing it silently deleted the entire companions and
        # gate-gap section, so "nobody could look" rendered as "no gaps". That is the
        # unavailable-as-success class, inside the report whose whole purpose is to
        # expose it.
        v["setup_todo"].append(
            f"The companion registry could not be read, so this handshake says nothing "
            f"about which gates have a tool behind them: {exc}. Fix "
            f".ddflow/companions.toml (or `ddflow companions`, which prints the same "
            f"error) — until then, treat every gate as unserved rather than served."
        )
    try:
        from ..core import progress as PR
        from ..core.model import fold
        from ..core.schedule import plan
        from ..infra.log import EventLog, effective_agent_id
        from ..services import importer as IM
        from ..services import leases as L

        # `effective_agent_id`, not `cfg.agent.id or ""` -- the latter falls to the
        # tree-derived default and reads neither DDFLOW_AGENT nor a declared name. The
        # identity here decides which items `plan()` counts as "already mine", so with
        # DDFLOW_AGENT set the handshake reported the connection's OWN claimed work as
        # someone else's, at the one moment the agent is told what to do next. B88's
        # sweep fixed two call sites and missed this one.
        log = EventLog(repo, effective_agent_id(repo, cfg, agent), log_cfg=cfg.log)
        events = log.read_all()
        st = fold(events, strict=False)
        p = plan(st, cfg, agent=log.agent_id)
        v["ready"], v["running"] = len(p.ready), len(p.running)
        v["queue_is_empty"] = not st.items
        # `rescan=False`: the queue-only half, which costs nothing because `st` is
        # already folded. The source re-scan is ~0.65 s on a real corpus -- cheap for a
        # command an operator typed, and not something to spend at every session start.
        # What the handshake does instead is TELL the agent to run the full check, and
        # only when the cheap half has already found something to act on.
        iv = IM.verify_import(repo, st, rescan=False)
        v["imported_total"] = iv.total
        v["imported_no_globs"] = len(iv.no_globs)
        v["imported_shipped_drift"] = len(iv.shipped_drift)
        v["blocked"] = len(p.blocked)
        v["open_bugs"] = sum(1 for b in st.bugs.values() if b.open)
        v["loops"] = len(PR.detect(events, st, cfg))
        # Trees that may hold work: one per tree (path compared after `normpath`), not
        # per item, and never a tree measured clean (B904edd649c). One that could not be
        # measured (None) counts: `recover` says to treat it as containing work until
        # someone has looked. A record with no tree is not a tree and is not counted.
        v["recoverable"] = len(
            {
                os.path.normpath(r.worktree)
                for r in L.scan(log, cfg, repo)
                if r.worktree and r.salvageable is not False
            }
        )
    except Exception:
        pass
    return v


def _instructions(repo: Path, agent: str = "", tier: str = DEFAULT_TIER) -> str:
    """What the client injects into the model's context on connect.

    **State-aware on purpose.** A fixed blurb describing a workflow the project has not
    adopted is noise the model learns to skip; the useful instruction is the next
    concrete action, and that depends on whether `.ddflow/` exists, whether a test
    command is set, whether a reviewer and the companion tools are configured, and
    whether anything is waiting to be recovered. This is the only place the server gets
    to speak unprompted, so it says the one thing that is true right now.

    **And it is a TEMPLATE, not a string literal.** Everything it says — the workflow,
    the reporting duties, which companion tools to reach for — is
    `templates/prompts/mcp_instructions.md`, overridable per project
    (`.ddflow/prompts/mcp_instructions.md`) or per config (`[prompts]
    mcp_instructions`). That is the difference between a tool whose behaviour you
    configure and one you have to fork.
    """
    from ..services import prompts as P

    vars_ = _instruction_vars(repo, agent)
    vars_["tool_tier_note"] = tier_note(tier, names=False)
    overrides: dict[str, str] = {}
    if vars_["adopted"]:
        try:
            from ..config import Config

            # Read by NAME, not by handing `prompts.__dict__` to the resolver. The
            # dead-knob ratchet greps for the knob being read and would have reported
            # this one as documented-but-never-read — correctly, because a bulk dict
            # pass is also how a knob gets renamed in config and silently stops working.
            path = Config.load(repo).prompts.mcp_instructions
            if path:
                overrides["mcp_instructions"] = path
        except Exception:
            pass
    try:
        tmpl = P.resolve("mcp_instructions", repo, overrides)
        return P.render(tmpl, **vars_).strip()
    except P.TemplateError as exc:
        # A broken override must not silence the server: say what is wrong, in the one
        # place the operator will see it, and still hand over the essentials.
        return (
            f"ddflow's instruction template could not be loaded: {exc}\n\n"
            "Call `ddflow_brief` for the state of the queue, and `ddflow_prompts` to "
            "inspect the template configuration."
        )


def _ok(mid: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def _err(mid: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def _text(body: str, *, error: bool = False, meta: dict | None = None) -> dict[str, Any]:
    """One tool result.

    An error carries `_meta.exit = 1` as well as `isError`, so the exit-code vocabulary
    the whole package speaks — 0 healthy · 1 failure · 2 nothing · 3 refused — holds at
    the protocol boundary too. Without it, a schema-level rejection (a missing required
    argument, an unknown one) arrived as exit 0, and an agent reading only the exit code
    could not tell a refused call from a successful one.
    """
    res: dict[str, Any] = {"content": [{"type": "text", "text": body}], "isError": error}
    if meta:
        res["_meta"] = meta
    elif error:
        res["_meta"] = {"exit": 1}
    return res


#: Tools that can run for minutes -- a gate command, a reviewer, a wait. A real stdio
#: server runs each in a worker PROCESS so the loop keeps answering: a 534 s unit_tests
#: run once timed out every other call (B5f209cb092); a reviewer that crashes or eats
#: the machine takes its worker down, not the server and the session's tools with it
#: (B55e649ca6e); and the worker sits in its own session, so a client that times out or
#: disconnects does not lose the run -- it finishes and records its outcome (B9abc247444).
#: A process, not a thread: the event log's caches are process-global and unlocked.
OFFLOADED = frozenset(
    {
        "ddflow_bisect",
        "ddflow_ci",
        "ddflow_gate_run",
        "ddflow_gate_verify",
        "ddflow_review",
        "ddflow_verify",
        "ddflow_wait",
    }
)

#: Workers running at once ON ONE CONNECTION. A burst beyond it is answered "busy"
#: (exit 2) rather than started: each is a whole interpreter. Per connection, not per
#: machine: what it bounds is one client's burst.
MAX_WORKERS = 4


def _offloaded(msg: dict[str, Any]) -> bool:
    params = msg.get("params")
    return (
        msg.get("method") == "tools/call"
        and msg.get("id") is not None
        and isinstance(params, dict)
        and isinstance(params.get("name"), str)  # a list name must not end the loop
        and params["name"] in OFFLOADED
    )


def _worker_stderr(repo: Path):
    """Where a worker's diagnostics go: a log of its own, never the client's stderr pipe.

    Inherited, a client that disconnected left the worker writing to a broken pipe, and
    a gate or reviewer child got SIGPIPE before the run could record its outcome."""
    import subprocess

    local = repo / ".ddflow" / "local"
    log = local / "mcp-workers.log"
    try:
        if (repo / ".ddflow").is_dir():
            local.mkdir(exist_ok=True)
            # Kept small: overwritten once past a megabyte rather than rotated.
            big = log.exists() and log.stat().st_size > 1 << 20
            return open(log, "w" if big else "a")  # the worker owns it
    except OSError as exc:
        print(f"ddflow mcp: worker log {log} unavailable ({exc})", file=sys.stderr)
    return subprocess.DEVNULL


def _offload(srv: Server, msg: dict[str, Any], send: Callable[[dict[str, Any]], None], busy: int):
    """Run one call in a worker process; a thread relays its frames. Returns the thread.

    The worker is given the connection's identity and location -- the only per-connection
    state a tool reads (`client_info` is a label, the footer counters a courtesy). A
    `notifications/cancelled` is deliberately NOT passed on: a review or gate a client
    stopped waiting for still finishes and records, which is the point (B9abc247444).
    """
    import subprocess
    import threading

    from ..infra import proc as P

    mid = msg.get("id")
    name = (msg.get("params") or {}).get("name", "")
    if busy >= MAX_WORKERS:
        send(
            _ok(
                mid,
                _text(
                    f"{name}: {busy} long calls are already running on this connection "
                    f"(at most {MAX_WORKERS}). Call again when one has answered.",
                    meta={"exit": 2},
                ),
            )
        )
        return None
    job = {
        "repo": str(srv.repo),
        "called_from": str(srv.called_from),
        "agent": srv.agent,
        "msg": msg,
    }
    # The worker must import THIS code, not whatever ddflow the path would find first.
    root = str(Path(__file__).resolve().parents[2])
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(filter(None, [root, os.environ.get("PYTHONPATH", "")])),
    }
    err = _worker_stderr(srv.repo)
    try:
        p = P.popen(
            [sys.executable, "-c", "from ddflow.surfaces.mcp import _worker_main as m; m()"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=err,
            text=True,
            env=env,
            start_new_session=True,
        )
        assert p.stdin is not None and p.stdout is not None
        p.stdin.write(json.dumps(job))
        p.stdin.close()
    except (OSError, ValueError) as exc:
        send(_ok(mid, _text(f"{name}: could not start its worker: {exc}", error=True)))
        return None
    finally:
        if err is not subprocess.DEVNULL:
            err.close()  # the worker holds its own copy

    def relay() -> None:
        answered = False
        for raw in p.stdout:
            try:
                frame = json.loads(raw)
            except json.JSONDecodeError:
                print(raw, end="", file=sys.stderr)
                continue
            answered = answered or ("method" not in frame and frame.get("id") == mid)
            try:
                send(frame)
            except Exception:  # the client is gone; keep draining so the worker never blocks
                pass
        rc = p.wait()
        if not answered:
            try:
                send(
                    _ok(
                        mid,
                        _text(
                            f"{name}'s worker process ended (exit {rc}) without an answer. "
                            "This server is still up. What the run recorded is in the log: "
                            "check ddflow_gate_status.",
                            error=True,
                        ),
                    )
                )
            except Exception:
                pass

    t = threading.Thread(target=relay, name=f"ddflow-{name}-{mid}", daemon=True)
    t.start()
    return t


def _worker_main() -> None:
    """A worker process: one offloaded call, read as a job from stdin (see `_offload`).

    Protocol frames go to a private copy of stdout, and fd 1 is pointed at stderr, so a
    stray print or a child's output can never corrupt them. A parent that died is no
    reason to stop: writes to it are dropped and the run finishes and records.
    """
    job = json.loads(sys.stdin.read())
    out = os.fdopen(os.dup(1), "w")
    os.dup2(2, 1)
    sys.stdout = sys.stderr

    def write(frame: dict[str, Any]) -> None:
        try:
            out.write(json.dumps(frame) + "\n")
            out.flush()
        except (OSError, ValueError):
            pass

    msg = job["msg"]
    srv = Server(Path(job["repo"]), job.get("agent", ""), called_from=Path(job["called_from"]))
    srv.notify = write
    try:
        reply = srv.handle(msg)
    except BaseException as exc:  # the caller is owed an answer either way
        print(traceback.format_exc(), file=sys.stderr)
        reply = _err(msg.get("id"), -32603, f"internal error: {exc}")
    if reply is not None:
        write(reply)
    try:
        out.close()
    except OSError:
        pass


def serve(
    repo: Path,
    stdin=None,
    stdout=None,
    *,
    called_from: Path | None = None,
    offload: bool | None = None,
) -> None:
    """Newline-delimited JSON-RPC over stdio, until EOF.

    Nothing may be written to stdout except protocol frames — a stray print corrupts
    the stream and the client sees a hung server. Diagnostics go to stderr.

    `offload` runs the `OFFLOADED` tools in worker processes; by default only a real
    stdio session does, and a caller handing in its own streams gets the plain loop.
    At EOF the loop waits for its workers' answers before returning.
    """
    srv = Server(repo, called_from=called_from)
    inp = stdin or sys.stdin
    outp = stdout or sys.stdout
    if offload is None:
        offload = stdin is None
    import threading

    out_lock = threading.Lock()

    def notify(frame: dict[str, Any]) -> None:
        with out_lock:
            outp.write(json.dumps(frame) + "\n")
            outp.flush()

    srv.notify = notify
    relays = []
    for raw in inp:
        line = raw.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as exc:
            notify(_err(None, -32700, f"parse error: {exc}"))
            continue
        if offload and _offloaded(msg):
            relays = [r for r in relays if r.is_alive()]
            if (relay := _offload(srv, msg, notify, len(relays))) is not None:
                relays.append(relay)
            continue
        try:
            reply = srv.handle(msg)
        except Exception as exc:
            print(traceback.format_exc(), file=sys.stderr)
            reply = _err(msg.get("id"), -32603, f"internal error: {exc}")
        if reply is not None:
            notify(reply)
    for relay in relays:
        relay.join()


def main(argv: list[str] | None = None) -> int:
    """Console-script entry point: ``ddflow-mcp [--repo PATH]``.

    Kept argument-light on purpose. An MCP client spawns this with no arguments and a
    working directory, so the default path -- resolve the repository from the cwd, and
    from there to the PRIMARY checkout even if the cwd is a linked worktree -- has to
    be the one that needs no configuration. ``DDFLOW_REPO`` overrides for clients that
    spawn servers from a fixed directory.
    """
    import argparse

    ap = argparse.ArgumentParser(
        prog="ddflow-mcp",
        description="ddflow MCP stdio server. Add to your agent's MCP config as:\n"
        '  {"mcpServers": {"ddflow": {"command": "uvx", '
        '"args": ["ddflow-mcp"]}}}',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "--repo",
        default=os.environ.get("DDFLOW_REPO", ""),
        help="repository root (default: cwd, resolved to the primary checkout)",
    )
    ap.add_argument("--version", action="store_true")
    args = ap.parse_args(argv)
    if args.version:
        print(SERVER_INFO["version"])
        return 0

    start = Path(args.repo) if args.repo else Path.cwd()
    try:
        from ..infra.worktree import repo_root

        repo = repo_root(start)
    except Exception:
        # Not a git repository, or git is absent. Serve anyway: `ddflow_doctor` will
        # say so in a way the model can read and relay, which is far more useful than
        # a server that refuses to start and shows the client only "exited 1".
        repo = start.resolve()
    # `start` is passed ON, not discarded. It is the caller's ACTUAL location -- the
    # worktree an agent harness spawned this server in -- and `repo` is deliberately the
    # primary so every worktree shares one event log. Resolving and forgetting meant
    # `claim` over MCP never saw that the caller was already isolated, so worktree
    # ADOPTION was unreachable from the one surface the harnesses it was written for
    # actually drive.
    serve(repo, called_from=start)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
