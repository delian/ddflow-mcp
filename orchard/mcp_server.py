"""An MCP stdio server for Orchard — implemented in the standard library alone.

Why hand-rolled rather than `pip install mcp`: portability is the entire point of this
project. A workflow kernel that only installs where a package index is reachable is not
portable, and the MCP stdio transport is newline-delimited JSON-RPC 2.0 — a few hundred
lines, fully specified, and stable. Taking a dependency to avoid writing them would
trade the property we are optimising for against a convenience we do not need.

The tool surface is deliberately **the same functions the CLI calls**. MCP is a second
door onto one implementation, never a second implementation. That is what keeps an
agent driving Orchard over MCP and an agent driving it over a shell from diverging —
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

import io
import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any

from .cli import main as cli_main

SUPPORTED_PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
SERVER_INFO = {"name": "orchard", "version": "0.1.0", "title": "Orchard work-queue kernel"}

#: Tool surface. Each entry maps an MCP tool onto an argv the CLI already understands,
#: so there is exactly one implementation of every operation.
#: (description, {property: (json_type, description, required)}, argv builder)
TOOLS: dict[str, dict[str, Any]] = {
    "orchard_brief": {
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
                "Also scan for crashed agents' worktrees and lead with them. Worth it "
                "at session start: unclaimed work left by a dead process is the one "
                "thing to know BEFORE picking up something new.",
                False,
            ),
        },
        "argv": lambda a: [
            "brief",
            *_opt("--item", a),
            *_opt("--phase", a),
            *(["--check-recovery"] if a.get("check_recovery") else []),
        ],
    },
    "orchard_next": {
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
        "argv": lambda a: ["--json", "next", *_opt("--phase", a), *_opt("--kind", a)],
    },
    "orchard_claim": {
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
            "force": (
                "boolean",
                "Override a refusal. Legitimate for exactly one thing: retrying after "
                "`orchard_recover` has told you a crashed agent's worktree holds "
                "nothing. Forcing past a dependency or a live lease is how two agents "
                "end up writing the same file, and the override is recorded either way.",
                False,
            ),
        },
        "argv": lambda a: [
            "--json",
            "claim",
            a["id"],
            *_opt("--globs", a),
            *_opt("--note", a),
            *(["--no-worktree"] if a.get("no_worktree") else []),
            *(["--force"] if a.get("force") else []),
        ],
    },
    "orchard_heartbeat": {
        "description": (
            "Renew the lease on an item. Call periodically during long work, "
            "or the lease expires and another agent may take the item."
        ),
        "properties": {"id": ("string", "Item id.", True)},
        "argv": lambda a: ["--json", "heartbeat", a["id"]],
    },
    "orchard_gate_status": {
        "description": (
            "Where an item stands in its quality pipeline, which gate is next, and the "
            "instruction for that gate. Gates marked '?' did not run — that is a coverage "
            "gap, never a pass."
        ),
        "properties": {"id": ("string", "Item id.", True)},
        "argv": lambda a: ["gate", "status", a["id"]],
    },
    "orchard_gate_run": {
        "description": (
            "Execute a command gate (tests, linters) and record the result "
            "with its evidence. Agent gates cannot be run this way; they are "
            "recorded with orchard_gate_record."
        ),
        "properties": {
            "id": ("string", "Item id.", True),
            "gate": ("string", "Gate id, e.g. unit_tests.", True),
        },
        "argv": lambda a: ["--json", "gate", "run", a["id"], a["gate"]],
    },
    "orchard_gate_record": {
        "description": (
            "Record the outcome of a gate you performed (research, a review, a bug hunt). "
            "outcome is one of passed/failed/unavailable/partial/skipped. "
            "IMPORTANT: if a reviewer or tool could not run, record 'unavailable' with a "
            "reason — recording it as 'passed' is how an entire review silently vanishes. "
            "Pass the reviewer's model so family independence can be checked."
        ),
        "properties": {
            "id": ("string", "Item id.", True),
            "gate": ("string", "Gate id.", True),
            "outcome": ("string", "passed | failed | unavailable | partial | skipped", True),
            "reason": ("string", "Required for failed/unavailable/partial/skipped.", False),
            "evidence": ("string", "What you ran and what it said. Required by some gates.", False),
            "model": ("string", "Model that performed it, e.g. 'gemini-2.5-pro'.", False),
            "command": (
                "string",
                "The command you actually ran. This and `exit_code` are what make an "
                "outcome evidence rather than an assertion; a gate listed in "
                "`gates.evidence_required` is rejected without them.",
                False,
            ),
            "exit_code": ("string", "That command's exit code.", False),
            "output_file": (
                "string",
                "Path to its full output. A digest is recorded, so the claim can be "
                "checked against the file later rather than taken on trust.",
                False,
            ),
        },
        "argv": lambda a: (
            [
                "--json",
                "gate",
                "record",
                a["id"],
                a["gate"],
                "--outcome",
                a.get("outcome", "passed"),
                *_opt("--reason", a),
                *_opt("--evidence", a),
                *_opt("--model", a),
                *_opt("--command", a),
                *_opt("--exit-code", a, "exit_code"),
                *_opt("--output-file", a, "output_file"),
            ]
        ),
    },
    "orchard_gate_skip": {
        "description": (
            "Skip a gate ON THE RECORD, with a mandatory reason. This is the auditable "
            "escape hatch, and it is the one to reach for: `gates.require_outcome` "
            "means a gate left silent BLOCKS completion, so the alternative to skipping "
            "is forcing past everything at once. A skip names the single step you are "
            "dropping and why, and that reason is in the event log permanently. "
            "Skipping a gate listed in `gates.required` still blocks — those are not "
            "optional."
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
        "argv": lambda a: [
            "--json",
            "gate",
            "skip",
            a["id"],
            a["gate"],
            "--reason",
            a.get("reason", ""),
        ],
    },
    "orchard_complete": {
        "description": (
            "Finish an item. Refuses (exit 3) when a required gate has not passed, when a "
            "phase still has open tasks, or when no reviewer came from a different model "
            "family than the author. Pass your own model as 'model'."
        ),
        "properties": {
            "id": ("string", "Item id.", True),
            "sha": ("string", "Commit sha this shipped as.", False),
            "model": ("string", "The AUTHOR's model.", False),
            "force": (
                "boolean",
                "Complete over unmet conditions. Every one is recorded in the event "
                "log as overridden, so this is visible forever rather than being the "
                "quiet way past a gate. Prefer `orchard_gate_skip` with a reason: it "
                "names the single step you are dropping instead of all of them.",
                False,
            ),
        },
        "argv": lambda a: [
            "--json",
            "complete",
            a["id"],
            *_opt("--sha", a),
            *_opt("--model", a),
            *(["--force"] if a.get("force") else []),
        ],
    },
    "orchard_merge": {
        "description": (
            "Merge an item's branch into the base branch from the primary "
            "checkout, without ever switching its branch."
        ),
        "properties": {
            "id": ("string", "Item id.", True),
            "message": ("string", "Merge commit message.", False),
            "keep": ("boolean", "Keep the worktree after merging, for inspection.", False),
            "allow_dirty": (
                "boolean",
                "Merge although the worktree has uncommitted changes. They are NOT "
                "included — that is the point of the refusal. Only pass this once you "
                "have looked at what is dirty and decided it is build output.",
                False,
            ),
        },
        "argv": lambda a: [
            "--json",
            "merge",
            a["id"],
            *_opt("--message", a),
            *(["--keep"] if a.get("keep") else []),
            *(["--allow-dirty"] if a.get("allow_dirty") else []),
        ],
    },
    "orchard_phase_add": {
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
                "Comma-separated path globs this phase writes. Set them: they are what "
                "lets two agents work different phases in parallel safely, and the "
                "phase's own dependencies are INHERITED by every task inside it.",
                False,
            ),
            "body": ("string", "Detail, acceptance criteria, context.", False),
            "tags": ("string", "Comma-separated tags.", False),
            "priority": ("integer", "Lower is offered first (default 100).", False),
        },
        "argv": lambda a: (
            [
                "--json",
                "phase",
                "add",
                a["id"],
                *_opt("--title", a),
                *_opt("--needs", a),
                *_opt("--globs", a),
                *_opt("--body", a),
                *_opt("--tags", a),
                *_opt("--priority", a),
            ]
        ),
    },
    "orchard_split": {
        "description": (
            "Split an item into sub-tasks IN PLACE when the work turns out to be two "
            "things. Use this the moment you discover it — mid-task discovery is the "
            "normal case, not an exception.\n\n"
            "The original keeps its id and history and becomes an umbrella that "
            "completes when its children do; closing it and opening two new ones "
            "instead would lose the thread between what was planned and what happened. "
            "Children inherit the parent's globs, so give each its own afterwards if "
            "they write different files — until then they cannot run in parallel."
        ),
        "properties": {
            "id": ("string", "The item to split.", True),
            "into": ("string", "Comma-separated 'sub-id=title' pairs. At least two.", True),
            "globs": ("string", "Globs for the children (default: inherit).", False),
            "needs": (
                "string",
                "Dependencies for the FIRST child. The others chain from it if you set "
                "theirs with orchard_update.",
                False,
            ),
        },
        "argv": lambda a: [
            "--json",
            "split",
            a["id"],
            *[
                arg
                for spec in str(a.get("into", "")).split(",")
                if spec.strip()
                for arg in ("--into", spec.strip())
            ],
            *_opt("--globs", a),
            *_opt("--needs", a),
        ],
    },
    "orchard_task_add": {
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
                "Owning phase id OR another TASK's id — a task parent makes this a "
                "SUB-TASK, which carries its own globs and dependencies and runs in "
                "parallel with its siblings like any other task. Same field as "
                "`phase`; both names exist because the CLI has both, and an argument "
                "that exists in one surface and not the other is a trap.",
                False,
            ),
            "title": ("string", "One-line description.", False),
            "needs": ("string", "Comma-separated ids this task depends on.", False),
            "globs": ("string", "Comma-separated path globs this task writes.", False),
            "body": ("string", "Detail and acceptance criteria.", False),
            "tags": ("string", "Comma-separated tags.", False),
            "priority": ("integer", "Lower is offered first (default 100).", False),
        },
        "argv": lambda a: (
            [
                "--json",
                "task",
                "add",
                a["id"],
                "--phase",
                a.get("parent") or a.get("phase", ""),
                *_opt("--title", a),
                *_opt("--needs", a),
                *_opt("--globs", a),
                *_opt("--body", a),
                *_opt("--tags", a),
                *_opt("--priority", a),
            ]
        ),
    },
    "orchard_lesson_add": {
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
            "tags": ("string", "Comma-separated tags.", False),
            "seen_in": (
                "string",
                "Comma-separated item ids where this was hit. What makes a lesson "
                "checkable later instead of merely memorable.",
                False,
            ),
            "supersedes": (
                "string",
                "Comma-separated lesson ids this replaces. The old one is retired, not "
                "deleted — retiring is how the corpus stops growing without losing the "
                "record of what was once believed.",
                False,
            ),
        },
        "argv": lambda a: (
            [
                "--json",
                "lesson",
                "add",
                *_opt("--id", a),
                "--title",
                a.get("title", ""),
                *_opt("--rule", a),
                *_opt("--why", a),
                *_opt("--how", a),
                *_opt("--tags", a),
                *_opt("--seen-in", a, "seen_in"),
                *_opt("--supersedes", a),
            ]
        ),
    },
    "orchard_lesson_search": {
        "description": (
            "Search past lessons by relevance (BM25). Use before starting "
            "work, and whenever something surprises you."
        ),
        "properties": {
            "query": ("string", "What you are about to do, in words.", True),
            "limit": ("integer", "Max results (default 5).", False),
        },
        "argv": lambda a: (
            ["--json", "lesson", "search", a.get("query", "")]
            + (["--limit", str(a["limit"])] if a.get("limit") else [])
        ),
    },
    "orchard_research_add": {
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
        "argv": lambda a: (
            [
                "--json",
                "research",
                *_opt("--id", a),
                "--question",
                a.get("question", ""),
                "--verdict",
                a.get("verdict", "THEORETICAL"),
                *_opt("--claim", a),
                *_opt("--falsifier", a),
                *_opt("--probe", a),
                *_opt("--probe-output", a, "probe_output"),
                *_opt("--sources", a),
                *_opt("--mechanism", a),
                *_opt("--budget", a),
                *_opt("--item", a),
            ]
        ),
    },
    "orchard_bug_fixed": {
        "description": (
            "Close a bug. Requires the name of the regression test that would "
            "catch it again — write the test, watch it FAIL against the "
            "unfixed code, then close."
        ),
        "properties": {
            "id": ("string", "Bug id.", True),
            "regression_test": ("string", "Test that now guards this.", True),
            "lesson_title": ("string", "Capture a lesson at the same time.", False),
            "lesson_rule": (
                "string",
                "The lesson in full — the transferable rule, not the incident. A "
                "future agent on a different task has to be able to apply it.",
                False,
            ),
            "lesson": ("string", "Id of an EXISTING lesson this bug belongs to.", False),
        },
        "argv": lambda a: (
            [
                "--json",
                "bug",
                "fixed",
                a["id"],
                "--regression-test",
                a.get("regression_test", ""),
                *_opt("--lesson-title", a, "lesson_title"),
                *_opt("--lesson-rule", a, "lesson_rule"),
                *_opt("--lesson", a),
            ]
        ),
    },
    "orchard_recover": {
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
                "Act on the advice: release the leases and remove the worktrees this "
                "reports as holding nothing. It only ever touches a tree MEASURED as "
                "having no uncommitted and no unmerged work — one that could not be "
                "measured is never removed, because 'could not tell' is not 'empty'.",
                False,
            ),
        },
        "argv": lambda a: [
            "--json",
            "recover",
            *_opt("--item", a),
            *(["--apply"] if a.get("apply") else []),
        ],
    },
    "orchard_board": {
        "description": "The whole work queue as a readable board, with the critical path.",
        "properties": {"phase": ("string", "Restrict to one phase.", False)},
        "argv": lambda a: ["board", *_opt("--phase", a)],
    },
    "orchard_progress": {
        "description": (
            "What work has ACTUALLY been done, aggregated from the event log: attempts "
            "per item, wall-clock held, gate runs, commits produced, and who did them. "
            "Use it to answer 'how much effort has gone into this' and to see an item's "
            "full gate history including the outcomes that were not passes."
        ),
        "properties": {"id": ("string", "One item, with its per-attempt detail.", False)},
        "argv": lambda a: ["--json", "progress", *([a["id"]] if a.get("id") else [])],
    },
    "orchard_loops": {
        "description": (
            "Detect circular references and runtime loops: dependency cycles, an item "
            "claimed and given up over and over, a gate whose verdict keeps flipping, "
            "work completed and reopened repeatedly, duplicate items writing the same "
            "files, and a queue where events keep arriving but nothing advances. "
            "CALL THIS WHEN WORK FEELS REPETITIVE — it is the check that tells you to "
            "stop and re-plan rather than trying the same thing again. Returns [] when "
            "there is nothing wrong."
        ),
        "properties": {},
        "argv": lambda a: ["--json", "loops"],
    },
    "orchard_cleanup": {
        "description": (
            "Classify every Orchard worktree and branch: merged (safe to remove), "
            "unmerged (carries commits nobody landed), dirty (uncommitted edits — a "
            "human looks), orphan, or stale branch. Reports by default; with "
            "apply=true it removes merged worktrees and branches and lands commits for "
            "items the queue already considers done. A dirty tree is NEVER touched "
            "automatically, whatever you pass — it is the only thing here that exists "
            "nowhere else."
        ),
        "properties": {"apply": ("boolean", "Perform the safe actions.", False)},
        "argv": lambda a: ["cleanup", *(["--apply"] if a.get("apply") else ["--json"])],
    },
    "orchard_recall": {
        "description": (
            "'HAVE WE BEEN HERE BEFORE?' — one search across everything this project "
            "remembers: architectural decisions, lessons learned, research verdicts, "
            "past bugs, similar tasks, and the operator's own earlier prompts.\n\n"
            "CALL THIS BEFORE STARTING ANY NON-TRIVIAL WORK. It exists so the operator "
            "does not have to say the same thing twice and you do not have to learn "
            "the same thing twice. Results are labelled by kind, because a binding "
            "decision, a transferable lesson and a prompt from three weeks ago should "
            "change what you do in different ways. A decision marked superseded names "
            "its replacement — follow the replacement."
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
                "Comma-separated subset: decisions,lessons,research,bugs,"
                "items,prompts. Default: all.",
                False,
            ),
        },
        "argv": lambda a: [
            "--json",
            "recall",
            a.get("query", ""),
            *(["--limit", str(a["limit"])] if a.get("limit") else []),
            *_opt("--sources", a),
            *(["--max-chars", str(a["max_chars"])] if a.get("max_chars") else []),
        ],
    },
    "orchard_status": {
        "description": (
            "The state of the whole project in one answer: how many tasks are done and "
            "which, what is in flight and who holds it, what is ready to start, what is "
            "blocked, how many agent-hours and commits went in, and whether anything is "
            "looping or waiting to be recovered. This is the tool for 'what is the "
            "status of this project?' and 'what has been completed?'."
        ),
        "properties": {},
        "argv": lambda a: ["--json", "status"],
    },
    "orchard_decision_add": {
        "description": (
            "Record an architectural decision so the project stays consistent and the "
            "reasoning survives. Use when you or the operator settle a question about "
            "HOW the software is built — a data representation, a boundary, a library "
            "choice, an invariant.\n\n"
            "ALWAYS set `globs` to the code it governs: that is what lets the decision "
            "be surfaced automatically to whoever works those files later, instead of "
            "only being findable by someone who already suspects it exists. Record "
            "`alternatives` too — without it the next agent re-proposes what was "
            "rejected."
        ),
        "properties": {
            "id": (
                "string",
                "Stable id, e.g. 'D1'. Choose one: `supersedes`, commit messages and "
                "docs all reference it, and a generated id cannot be cited in advance.",
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
                "proposed | accepted (default) | superseded. 'proposed' records a "
                "decision the operator has not ratified, which is honest about its "
                "standing rather than presenting it as settled.",
                False,
            ),
        },
        "argv": lambda a: [
            "--json",
            "decision",
            "add",
            *_opt("--id", a),
            "--title",
            a.get("title", ""),
            "--decision",
            a.get("decision", ""),
            *_opt("--context", a),
            *_opt("--consequences", a),
            *_opt("--alternatives", a),
            *_opt("--globs", a),
            *_opt("--by", a),
            *_opt("--supersedes", a),
            *_opt("--item", a),
            *_opt("--tags", a),
            *_opt("--status", a),
        ],
    },
    "orchard_decision_list": {
        "description": (
            "Every architectural decision in force. Superseded ones are "
            "hidden unless you ask for them — they are kept, never deleted, "
            "because how the architecture got here is what a rebuild needs."
        ),
        "properties": {"all": ("boolean", "Include superseded decisions.", False)},
        "argv": lambda a: ["--json", "decision", "list", *(["--all"] if a.get("all") else [])],
    },
    "orchard_decision_applicable": {
        "description": (
            "The architectural decisions that govern a specific item's declared files. "
            "CALL THIS BEFORE IMPLEMENTING: it is how a decision reaches the person "
            "writing the code, without them having to know it exists. Returns "
            "project-wide decisions too."
        ),
        "properties": {"id": ("string", "Item id.", True)},
        "argv": lambda a: ["--json", "decision", "applicable", a["id"]],
    },
    "orchard_decision_supersede": {
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
        "argv": lambda a: [
            "--json",
            "decision",
            "supersede",
            a["id"],
            "--by",
            a.get("by", ""),
            *_opt("--reason", a),
        ],
    },
    "orchard_replay": {
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
                "Re-resolve every recorded commit sha against this repository and "
                "report the ones that are gone. A reconstruction citing shas nobody "
                "can resolve is a narrative, not a record.",
                False,
            ),
        },
        "argv": lambda a: [
            "replay",
            *_opt("--out", a),
            *(["--verify"] if a.get("verify") else []),
        ],
    },
    "orchard_render": {
        "description": (
            "Regenerate the human-readable markdown views (queue, lessons, "
            "research) under docs/orchard/."
        ),
        "properties": {
            "out": ("string", "Directory for the generated views (default: docs/orchard).", False),
            "show": (
                "string",
                "Print ONE view instead of writing files: lessons, research, or board. "
                "This is what the orchard:// resources are served from.",
                False,
            ),
        },
        "argv": lambda a: (
            ["render", "--show", a["show"]]
            if a.get("show")
            else ["--json", "render", *_opt("--out", a)]
        ),
    },
    "orchard_rebuild": {
        "description": (
            "Re-derive the search index from the event log. The index is a "
            "disposable cache; this is never a data-loss operation."
        ),
        "properties": {},
        "argv": lambda a: ["--json", "rebuild"],
    },
    "orchard_companions": {
        "description": (
            "Which companion MCP servers serve this project's gates, which are "
            "installed on this machine, and which are wired into an agent's config. "
            "Orchard imposes the pipeline; it does not perform the judgement inside "
            "most gates — `standards` wants an automated standards review, `research` "
            "wants documentation to check a claim against, `rules` wants memory. A "
            "project with none of them has agent gates passing on assertion alone. "
            "Exit 2 means a default companion is missing or unregistered. Read-only: "
            "it detects and advises, it never installs anything."
        ),
        "properties": {
            "no_probe": ("boolean", "Skip the detection probes (faster, less certain).", False),
        },
        "argv": lambda a: (
            ["--json", "companions", "list"] + (["--no-probe"] if a.get("no_probe") else [])
        ),
    },
    "orchard_companions_add": {
        "description": (
            "Register companion MCP servers that are ALREADY installed into an "
            "agent's MCP config, merging rather than overwriting what is there. "
            "Refuses (exit 3) to register one that is not installed, because that "
            "writes a launch command which fails mid-task, at the moment a gate told "
            "the agent to reach for it."
        ),
        "properties": {
            "id": ("string", "Comma-separated ids; default: every installed one.", False),
            "agents": ("string", "Comma-separated agent keys (default: claude).", False),
        },
        "argv": lambda a: (
            ["--json", "companions", "add"]
            + (["--id", a["id"]] if a.get("id") else [])
            + (["--agents", a["agents"]] if a.get("agents") else [])
        ),
    },
    "orchard_bug_found": {
        "description": (
            "Report a bug the moment you find it, BEFORE fixing it. Recording it first "
            "is what makes the fix accountable: `orchard_bug_fixed` refuses to close "
            "one without naming the regression test, so a bug that was never opened is "
            "a fix that never had to prove itself. Bug hunts that record nothing look "
            "identical to bug hunts that found nothing."
        ),
        "properties": {
            "id": ("string", "Stable id, e.g. 'B1'. You will cite it when closing.", False),
            "summary": ("string", "What is wrong, in one line.", True),
            "item": ("string", "The task it was found in or affects.", False),
        },
        "argv": lambda a: [
            "--json",
            "bug",
            "found",
            *_opt("--id", a),
            "--summary",
            a.get("summary", ""),
            *_opt("--item", a),
        ],
    },
    "orchard_session_note": {
        "description": (
            "Record something that happened during a session which is neither an "
            "operator prompt nor a decision — a surprise, a dead end, why you changed "
            "approach. It goes into the reconstruction alongside the prompts, and a "
            "dead end recorded is a dead end nobody walks down twice."
        ),
        "properties": {
            "session": ("string", "Session id from orchard_session_start.", True),
            "text": ("string", "The note.", True),
            "item": ("string", "Item it concerns.", False),
        },
        "argv": lambda a: [
            "--json",
            "session",
            "note",
            a.get("session", ""),
            "--text",
            a.get("text", ""),
            *_opt("--item", a),
        ],
    },
    "orchard_session_end": {
        "description": (
            "Close a session with a summary of what it achieved. The summary is what a "
            "later reader sees before deciding whether to open the whole transcript, "
            "so write it for someone who was not there."
        ),
        "properties": {
            "session": ("string", "Session id.", True),
            "summary": ("string", "What this session achieved.", False),
        },
        "argv": lambda a: [
            "--json",
            "session",
            "end",
            a.get("session", ""),
            *_opt("--summary", a),
        ],
    },
    "orchard_decision_show": {
        "description": (
            "Read ONE architectural decision in full — its context, what was decided, "
            "the consequences, and what was rejected. `orchard_decision_list` gives "
            "you the titles; this is what you read before working against one, and "
            "especially before proposing something it already considered."
        ),
        "properties": {"id": ("string", "Decision id.", True)},
        "argv": lambda a: ["--json", "decision", "show", a["id"]],
    },
    "orchard_prompts": {
        "description": (
            "Inspect the prompt templates this project uses, and where each comes from "
            "(shipped default, project override, or an explicit config path). Use "
            "`eject` to copy the shipped ones into .orchard/prompts/ so the project can "
            "edit them as plain text — reviewer instructions and workflow commands are "
            "operator-tunable behaviour, not code."
        ),
        "properties": {
            "action": ("string", "list (default), show, or eject.", False),
            "name": ("string", "Template name, for show/eject.", False),
        },
        "argv": lambda a: (
            ["--json", "prompts", "list"]
            if a.get("action", "list") == "list"
            else ["prompts", a["action"], *([a["name"]] if a.get("name") else [])]
        ),
    },
    "orchard_hooks": {
        "description": (
            "Inspect or install the enforcement git hook — the one layer of this "
            "workflow that does not depend on the agent agreeing. It refuses a commit "
            "touching paths no live lease of yours covers. `status` reports whether it "
            "is installed AND whether the policy actually blocks, since a block policy "
            "with no hook installed enforces nothing."
        ),
        "properties": {"action": ("string", "status (default), install, uninstall.", False)},
        "argv": lambda a: ["--json", "hooks", a.get("action", "status")],
    },
    "orchard_doctor": {
        "description": (
            "Integrity and health check: log corruption, dependency cycles, "
            "unknown dependencies, orphaned worktrees, stale index."
        ),
        "properties": {},
        "argv": lambda a: ["doctor"],
    },
    "orchard_cadence": {
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
        "argv": lambda a: ["--json", "cadence", *_opt("--ran", a), *_opt("--note", a)],
    },
    "orchard_session_prompt": {
        "description": (
            "Record the operator's prompt verbatim. This is what makes the project "
            "reconstructible from the log alone if everything else is lost. Secrets are "
            "redacted before anything touches disk. Call it once per operator turn."
        ),
        "properties": {
            "session": ("string", "Session id from orchard_session_start.", True),
            "text": ("string", "The prompt, verbatim.", True),
            "item": ("string", "Item it concerns.", False),
        },
        "argv": lambda a: (
            [
                "--json",
                "session",
                "prompt",
                a.get("session", ""),
                "--text",
                a.get("text", ""),
                *_opt("--item", a),
            ]
        ),
    },
    "orchard_setup": {
        "description": (
            "Install Orchard into this repository: creates .orchard/, writes the driver "
            "and the AGENTS.md section, and registers nothing else. Run this ONCE per "
            "project, then set your test command with orchard_configure. Safe to re-run "
            "— it updates a managed block and leaves your own prose alone."
        ),
        "properties": {
            "agents": (
                "string",
                "Comma-separated agents to write driver deltas for: "
                "claude,gemini,codex,copilot,kilo. Default: all.",
                False,
            )
        },
        "argv": lambda a: ["adopt", *_opt("--agents", a)],
    },
    "orchard_configure": {
        "description": (
            "Read or write .orchard/config.toml. With no arguments it prints every "
            "knob, its value, its source and what it does. With `toml`, it APPENDS that "
            "TOML to the config — the usual use is setting your project's test command:\n"
            '  [gate.unit_tests]\n  command = "pytest -q"\n'
            "This is how a project is configured without a shell."
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
        },
        "argv": lambda a: (
            ["config", "--set", a["set"], a.get("value", "")]
            if a.get("set")
            else ["config", "--append-toml", a["toml"]]
            if a.get("toml")
            else ["config", "--explain", *_opt("--filter", a)]
        ),
    },
    "orchard_reviewers_detect": {
        "description": (
            "Probe well-known local ports for an OpenAI-compatible model server (ollama, "
            "vLLM, LM Studio, llama.cpp, sglang) and report what is serving, with each "
            "model's pretraining family. Use this to find a reviewer from a DIFFERENT "
            "family than yourself — which the critic gate requires. Pass write=true to "
            "add what it finds to .orchard/config.toml."
        ),
        "properties": {
            "write": ("boolean", "Append the discovered reviewers to the config.", False)
        },
        "argv": lambda a: ["reviewers", "detect"] + (["--write"] if a.get("write") else []),
    },
    "orchard_reviewers_list": {
        "description": "Show the configured reviewers, their families and which gates they serve.",
        "properties": {},
        "argv": lambda a: ["reviewers", "list"],
    },
    "orchard_review": {
        "description": (
            "Run the configured cross-family reviewer over an item's diff and record the "
            "result. This is the critic gate performed by Orchard rather than claimed by "
            "you — it calls a real endpoint, parses the verdict, and records the evidence. "
            "If no reviewer is configured, or the endpoint is unreachable, or the model "
            "returns no verdict, it records UNAVAILABLE and never a pass."
        ),
        "properties": {
            "id": ("string", "Item whose diff to review.", True),
            "gate": ("string", "Gate to record under: critic (default) or rubber_duck.", False),
            "intent": (
                "string",
                "What the change is MEANT to do. The reviewer flags where the diff "
                "and the intent disagree, so without it there is nothing to "
                "disagree with. Defaults to the item's title and body.",
                False,
            ),
            "base": ("string", "Ref to diff against (default: the item's base branch).", False),
            "context": ("string", "Extra context to hand the reviewer.", False),
        },
        "argv": lambda a: [
            "review",
            a["id"],
            *_opt("--gate", a),
            *_opt("--intent", a),
            *_opt("--base", a),
            *_opt("--context", a),
        ],
    },
    "orchard_show": {
        "description": (
            "Everything known about one phase or task: state, dependencies, declared "
            "globs, the lease and who holds it, the worktree path you can cd to, and "
            "every gate's outcome with its evidence. Use it to check your own work "
            "before calling orchard_complete."
        ),
        "properties": {"id": ("string", "Item id.", True)},
        "argv": lambda a: ["--json", "show", a["id"]],
    },
    "orchard_update": {
        "description": (
            "Change an item's fields. MOST IMPORTANT USE: widening `globs` when your "
            "work turns out to touch files outside what you claimed. Do that BEFORE "
            "writing them — the conflict detector and the commit hook both work from "
            "the declared globs, so an undeclared file is a file no one is protecting "
            "and the commit will be refused."
        ),
        "properties": {
            "id": ("string", "Item id.", True),
            "globs": ("string", "Comma-separated path globs this item writes.", False),
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
        },
        "argv": lambda a: [
            "--json",
            "update",
            a["id"],
            # `clearable`: passing "" here MEANS "empty this", which is how you break a
            # dependency cycle. Every other tool treats "" as "not supplied".
            *_opt("--globs", a, clearable=True),
            *_opt("--needs", a, clearable=True),
            *_opt("--tags", a, clearable=True),
            *_opt("--title", a),
            *_opt("--body", a),
            *_opt("--priority", a),
        ],
    },
    "orchard_abandon": {
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
                "Abandon although a sub-task is still open. Those sub-tasks do NOT "
                "become abandoned with it — decide about each, or they sit in the "
                "queue under a parent nobody will finish.",
                False,
            ),
        },
        "argv": lambda a: [
            "--json",
            "abandon",
            a["id"],
            "--reason",
            a.get("reason", ""),
            *(["--force"] if a.get("force") else []),
        ],
    },
    "orchard_remove": {
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
                "Remove although it still has open children, or although other items "
                "depend on it. Both leave the queue inconsistent in a way the "
                "scheduler then reports, so read the refusal before overriding it.",
                False,
            ),
        },
        "argv": lambda a: [
            "--json",
            "remove",
            a["id"],
            *_opt("--reason", a),
            *(["--force"] if a.get("force") else []),
        ],
    },
    "orchard_release": {
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
        "argv": lambda a: ["--json", "release", a["id"], *_opt("--note", a)],
    },
    "orchard_block": {
        "description": (
            "Mark an item blocked on something outside the queue — a missing decision, "
            "an upstream outage, a question for the operator. Better than silently "
            "leaving it claimed: a blocked item states its reason, while a claimed one "
            "that nobody is working just looks busy until the lease expires."
        ),
        "properties": {
            "id": ("string", "Item id.", True),
            "reason": ("string", "What it is waiting on.", True),
        },
        "argv": lambda a: ["--json", "block", a["id"], "--reason", a.get("reason", "")],
    },
    "orchard_session_start": {
        "description": "Open a session for provenance logging. Returns the session id.",
        "properties": {
            "model": ("string", "Your model id.", False),
            "tool": ("string", "Your harness, e.g. 'claude-code'.", False),
        },
        "argv": lambda a: ["--json", "session", "start", *_opt("--model", a), *_opt("--tool", a)],
    },
}


def _opt(
    flag: str, args: dict[str, Any], key: str | None = None, *, clearable: bool = False
) -> list[str]:
    """Build `--flag value`, or nothing when the caller did not supply one.

    ``clearable`` distinguishes **"not supplied"** from **"supplied as empty"**, which
    this conflated. An empty string was treated as absent, so over MCP a dependency
    could be added and never removed: `orchard_update(id="X", needs="")` silently did
    nothing, while `orchard update X --needs ""` cleared it. Breaking a dependency
    cycle is exactly the operation that needs this, and it is the one the loop detector
    tells you to perform.

    Off by default, and deliberately so: a client that fills every optional property
    with `""` would otherwise wipe fields it never meant to touch. Only `orchard_update`
    — whose entire job is to change fields — passes it.
    """
    k = key or flag.lstrip("-").replace("-", "_")
    if clearable and k in args and args[k] is not None:
        return [flag, str(args[k])]
    v = args.get(k)
    return [flag, str(v)] if v not in (None, "", []) else []


def _schema(spec: dict[str, Any]) -> dict[str, Any]:
    props = {
        name: {"type": t, "description": desc}
        for name, (t, desc, _req) in spec["properties"].items()
    }
    required = [n for n, (_t, _d, req) in spec["properties"].items() if req]
    return {
        "type": "object",
        "properties": props,
        **({"required": required} if required else {}),
        "additionalProperties": False,
    }


def _run_cli(repo: Path, argv: list[str]) -> tuple[int, str]:
    """Invoke the CLI in-process, capturing both streams.

    In-process rather than subprocess: it is ~40x faster per call, and it guarantees
    the MCP surface and the shell surface execute literally the same code, which is the
    property that stops them drifting.
    """
    out, err = io.StringIO(), io.StringIO()
    real_out, real_err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = out, err
    try:
        code = cli_main(["--repo", str(repo), *argv])
    except SystemExit as exc:
        code = int(exc.code or 0)
    except Exception:
        code = 1
        err.write(traceback.format_exc())
    finally:
        sys.stdout, sys.stderr = real_out, real_err
    body = out.getvalue()
    tail = err.getvalue()
    if tail:
        body = (body + "\n" if body else "") + tail
    return code, body.strip()


class Server:
    def __init__(self, repo: Path) -> None:
        self.repo = Path(repo)
        self.protocol = SUPPORTED_PROTOCOLS[0]

    def handle(self, msg: dict[str, Any]) -> dict[str, Any] | None:
        method = msg.get("method", "")
        mid = msg.get("id")
        if method == "initialize":
            want = (msg.get("params") or {}).get("protocolVersion", "")
            self.protocol = want if want in SUPPORTED_PROTOCOLS else SUPPORTED_PROTOCOLS[0]
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
                    "instructions": _instructions(self.repo),
                },
            )
        if method in ("notifications/initialized", "notifications/cancelled"):
            return None
        if method == "ping":
            return _ok(mid, {})
        if method == "tools/list":
            return _ok(
                mid,
                {
                    "tools": [
                        {"name": n, "description": s["description"], "inputSchema": _schema(s)}
                        for n, s in sorted(TOOLS.items())
                    ]
                },
            )
        if method == "tools/call":
            params = msg.get("params") or {}
            name = params.get("name", "")
            args = params.get("arguments") or {}
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
            unknown = sorted(set(args) - set(spec["properties"]))
            if unknown:
                return _ok(
                    mid,
                    _text(
                        f"unknown argument(s) for {name}: {', '.join(unknown)}. "
                        f"Known: {', '.join(sorted(spec['properties']))}",
                        error=True,
                    ),
                )
            try:
                argv = spec["argv"](args)
            except (KeyError, TypeError) as exc:
                return _ok(mid, _text(f"bad arguments: {exc}", error=True))
            code, body = _run_cli(self.repo, argv)
            # Exit 2 ("nothing to do") and 3 ("coordination refused") are RESULTS, not
            # errors: the model must read and act on them. Only 1 is a genuine failure.
            return _ok(mid, _text(body or f"(exit {code})", error=(code == 1), meta={"exit": code}))
        if method == "resources/list":
            return _ok(
                mid,
                {
                    "resources": [
                        {
                            "uri": "orchard://board",
                            "name": "Work queue",
                            "description": "The full queue with the critical path.",
                            "mimeType": "text/markdown",
                        },
                        {
                            "uri": "orchard://brief",
                            "name": "Session brief",
                            "description": "Budgeted session-start pack.",
                            "mimeType": "text/markdown",
                        },
                        {
                            "uri": "orchard://lessons",
                            "name": "Lessons",
                            "description": "Everything learned so far.",
                            "mimeType": "text/markdown",
                        },
                        {
                            "uri": "orchard://research",
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
            # table also carried a dead entry for `orchard://lessons` that the branch
            # above it shadowed, which is how a second path hides: nothing reads the
            # line, so nothing contradicts it.
            cmd = {
                "orchard://board": ["board"],
                "orchard://brief": ["brief"],
                "orchard://lessons": ["render", "--show", "lessons"],
                "orchard://research": ["render", "--show", "research"],
            }.get(uri)
            if not cmd:
                return _err(mid, -32602, f"unknown resource {uri!r}")
            _, body = _run_cli(self.repo, cmd)
            return _ok(mid, {"contents": [{"uri": uri, "mimeType": "text/markdown", "text": body}]})
        if method == "prompts/list":
            from . import prompts as P

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
                        for name, (title, desc, args) in sorted(P.COMMANDS.items())
                    ]
                },
            )
        if method == "prompts/get":
            from . import prompts as P

            params = msg.get("params") or {}
            name = params.get("name", "")
            args = params.get("arguments") or {}
            try:
                tmpl = P.resolve_command(name, self.repo)
                # Every declared argument is bound, empty when absent: the renderer is
                # strict about undefined names, and a command that raises because the
                # operator omitted an optional argument is a command nobody uses twice.
                declared = dict.fromkeys(P.COMMANDS[name][2], "")
                text = P.render(tmpl, **{**declared, **args, "test_gates": _test_gates(self.repo)})
            except P.TemplateError as exc:
                return _err(mid, -32602, str(exc))
            return _ok(
                mid,
                {
                    "description": P.COMMANDS[name][1],
                    "messages": [{"role": "user", "content": {"type": "text", "text": text}}],
                },
            )
        return _err(mid, -32601, f"method not found: {method}")


def _test_gates(repo: Path) -> list[str]:
    """Every configured gate that looks like a test suite, beyond `unit_tests`.

    Read from the project's own config so the `all-tests` command names the suites
    that actually exist here, rather than a generic list the reader has to translate.
    """
    try:
        from .config import Config
        from .gates import load_gates

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


def _instruction_vars(repo: Path) -> dict[str, Any]:
    """Everything `mcp_instructions.md` can render from.

    Gathered defensively: this runs inside the `initialize` handshake, which must
    succeed even in a repository that is broken, half-configured or not adopted at all.
    Every lookup that can fail contributes its own default rather than taking the whole
    handshake down, because a server that refuses to start cannot tell anyone why.

    It also must not WRITE anything — a handshake that adopts the repository is the bug
    this file already fixed once, and `Store` learned the same lesson separately.
    """
    adopted = (repo / ".orchard" / "config.toml").is_file()
    v: dict[str, Any] = {
        "adopted": adopted,
        "setup_todo": [],
        "companions": [],
        "missing_companions": [],
        "gate_gaps": [],
        "recoverable": 0,
        "ready": 0,
        "running": 0,
        "blocked": 0,
        "open_bugs": 0,
        "loops": 0,
        "task_pipeline": [],
        "require_outcome": True,
    }
    if not adopted:
        return v

    try:
        from .config import Config

        cfg = Config.load(repo)
    except Exception:
        return v
    v["task_pipeline"] = list(cfg.gates.task_pipeline)
    v["require_outcome"] = bool(cfg.gates.require_outcome)

    # Each block is independent, and a failure in one must not cost the others: a
    # project with a bad reviewer block should still be told what is ready to work.
    try:
        from .gates import load_gates

        ut = load_gates(repo, cfg).get("unit_tests")
        if not ut or not ut.command or "set [gate.unit_tests]" in ut.command:
            v["setup_todo"].append(
                "No test command is configured. Set it with `orchard_configure`: "
                '`[gate.unit_tests]` / `command = "<your test command>"`. Until then '
                "the unit_tests gate reports UNAVAILABLE and cannot pass."
            )
    except Exception:
        pass
    try:
        from .reviewer import load_reviewers

        if not load_reviewers(repo):
            v["setup_todo"].append(
                "No cross-family reviewer is configured, so the `critic` gate cannot "
                "run and `orchard_complete` will refuse. Call "
                "`orchard_reviewers_detect` with write=true — it finds a local model "
                "server if one is running."
            )
    except Exception:
        pass
    try:
        # `probe=False`: detection shells out, and the handshake is the one call an
        # agent waits on before it can do anything at all. Registration state is read
        # from config files and is free; whether the binary exists can wait for
        # `orchard_companions`, which is what the instruction tells it to call.
        from . import companions as CO

        statuses = CO.scan(repo, probe=False)
        v["companions"] = [
            {
                "id": st.companion.id,
                "title": st.companion.title,
                "gates": list(st.companion.gates),
                "state": st.state,
                "install": st.companion.install,
                "url": st.companion.url,
                "default": st.companion.default,
            }
            for st in statuses
        ]
        v["missing_companions"] = [
            c for c in v["companions"] if c["default"] and c["state"] != "registered"
        ]
        cover = CO.gate_coverage(repo, statuses, v["task_pipeline"])
        v["gate_gaps"] = [g for g, ids in cover.items() if not ids]
    except Exception:
        pass
    try:
        from . import lease as L
        from . import progress as PR
        from .events import EventLog
        from .model import fold
        from .schedule import plan

        log = EventLog(repo, cfg.agent.id or "")
        events = log.read_all()
        st = fold(events, strict=False)
        p = plan(st, cfg, agent=log.agent_id)
        v["ready"], v["running"] = len(p.ready), len(p.running)
        v["blocked"] = len(p.blocked)
        v["open_bugs"] = sum(1 for b in st.bugs.values() if b.open)
        v["loops"] = len(PR.detect(events, st, cfg))
        v["recoverable"] = len(L.scan(log, cfg, repo))
    except Exception:
        pass
    return v


def _instructions(repo: Path) -> str:
    """What the client injects into the model's context on connect.

    **State-aware on purpose.** A fixed blurb describing a workflow the project has not
    adopted is noise the model learns to skip; the useful instruction is the next
    concrete action, and that depends on whether `.orchard/` exists, whether a test
    command is set, whether a reviewer and the companion tools are configured, and
    whether anything is waiting to be recovered. This is the only place the server gets
    to speak unprompted, so it says the one thing that is true right now.

    **And it is a TEMPLATE, not a string literal.** Everything it says — the workflow,
    the reporting duties, which companion tools to reach for — is
    `templates/prompts/mcp_instructions.md`, overridable per project
    (`.orchard/prompts/mcp_instructions.md`) or per config (`[prompts]
    mcp_instructions`). That is the difference between a tool whose behaviour you
    configure and one you have to fork.
    """
    from . import prompts as P

    vars_ = _instruction_vars(repo)
    overrides: dict[str, str] = {}
    if vars_["adopted"]:
        try:
            from .config import Config

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
            f"Orchard's instruction template could not be loaded: {exc}\n\n"
            "Call `orchard_brief` for the state of the queue, and `orchard_prompts` to "
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


def serve(repo: Path, stdin=None, stdout=None) -> None:
    """Newline-delimited JSON-RPC over stdio, until EOF.

    Nothing may be written to stdout except protocol frames — a stray print corrupts
    the stream and the client sees a hung server. Diagnostics go to stderr.
    """
    srv = Server(repo)
    inp = stdin or sys.stdin
    outp = stdout or sys.stdout
    for raw in inp:
        line = raw.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as exc:
            outp.write(json.dumps(_err(None, -32700, f"parse error: {exc}")) + "\n")
            outp.flush()
            continue
        try:
            reply = srv.handle(msg)
        except Exception as exc:
            print(traceback.format_exc(), file=sys.stderr)
            reply = _err(msg.get("id"), -32603, f"internal error: {exc}")
        if reply is not None:
            outp.write(json.dumps(reply) + "\n")
            outp.flush()


def main(argv: list[str] | None = None) -> int:
    """Console-script entry point: ``orchard-mcp [--repo PATH]``.

    Kept argument-light on purpose. An MCP client spawns this with no arguments and a
    working directory, so the default path -- resolve the repository from the cwd, and
    from there to the PRIMARY checkout even if the cwd is a linked worktree -- has to
    be the one that needs no configuration. ``ORCHARD_REPO`` overrides for clients that
    spawn servers from a fixed directory.
    """
    import argparse

    ap = argparse.ArgumentParser(
        prog="orchard-mcp",
        description="Orchard MCP stdio server. Add to your agent's MCP config as:\n"
        '  {"mcpServers": {"orchard": {"command": "uvx", '
        '"args": ["orchard-mcp"]}}}',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "--repo",
        default=os.environ.get("ORCHARD_REPO", ""),
        help="repository root (default: cwd, resolved to the primary checkout)",
    )
    ap.add_argument("--version", action="store_true")
    args = ap.parse_args(argv)
    if args.version:
        print(SERVER_INFO["version"])
        return 0

    start = Path(args.repo) if args.repo else Path.cwd()
    try:
        from .worktree import repo_root

        repo = repo_root(start)
    except Exception:
        # Not a git repository, or git is absent. Serve anyway: `orchard_doctor` will
        # say so in a way the model can read and relay, which is far more useful than
        # a server that refuses to start and shows the client only "exited 1".
        repo = start.resolve()
    serve(repo)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
