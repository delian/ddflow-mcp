"""MCP tools: the loop: brief, next, claim, heartbeat, the gates, complete, merge.

One slice of the `TOOLS` registry, assembled in `surfaces/tools/__init__.py`."""

from __future__ import annotations

from typing import Any

from ._common import _api

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
    "ddflow_gate_list": {
        "description": (
            "The gates this project defines; with refuted=true, every gate recorded passed ON "
            "REFUTATION (its findings refuted with probes rather than a clean re-review), for "
            "the operator's spot-check."
        ),
        "properties": {
            "refuted": ("boolean", "List the gates passed on refutation instead.", False)
        },
        "api": lambda repo, a, agent: _api().gate_list(
            repo, refuted=bool(a.get("refuted")), agent=agent
        ),
        "payload": "text",
        "text": True,
        "kind": "gate.list",
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
            "umbrellas_completed",
            "umbrella_refused",
            "refuted_passes",
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
            "outside_globs_unknown",
            "merge_gate_human",
            "worktree",
            "worktree_removed",
        ),
    },
}
