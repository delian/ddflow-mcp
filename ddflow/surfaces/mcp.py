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

import io
import json
import os
import re
import sys
import traceback
from pathlib import Path
from typing import Any

SUPPORTED_PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
SERVER_INFO = {"name": "ddflow", "version": "0.1.0", "title": "ddflow work-queue kernel"}

#: Tool surface. Each entry maps an MCP tool onto an argv the CLI already understands,
#: so there is exactly one implementation of every operation.
#: (description, {property: (json_type, description, required)}, argv builder)
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
        "argv": lambda a: ["--json", "next", *_opt("--phase", a), *_opt("--kind", a)],
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
            "force": (
                "boolean",
                "Override a refusal. Legitimate for exactly one thing: retrying after "
                "`ddflow_recover` has told you a crashed agent's worktree holds "
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
    "ddflow_heartbeat": {
        "description": (
            "Renew the lease on an item. Call periodically during long work, "
            "or the lease expires and another agent may take the item."
        ),
        "properties": {"id": ("string", "Item id.", True)},
        "argv": lambda a: ["--json", "heartbeat", a["id"]],
    },
    "ddflow_gate_status": {
        "description": (
            "Where an item stands in its quality pipeline, which gate is next, and the "
            "instruction for that gate. Gates marked '?' did not run — that is a coverage "
            "gap, never a pass."
        ),
        "properties": {"id": ("string", "Item id.", True)},
        "argv": lambda a: ["gate", "status", a["id"]],
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
        "argv": lambda a: ["--json", "gate", "run", a["id"], a["gate"]],
    },
    "ddflow_gate_record": {
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
    "ddflow_gate_verify": {
        "description": (
            "Break what a gate guards and require it to NOTICE. Applies each mutation "
            "registered on the gate, runs it, requires a non-zero exit, and restores "
            "the file.\n\n"
            "This is the anti-vacuous-pass check turned on the checks themselves. A "
            "gate that cannot fail is worse than no gate: it reports success on every "
            "change and everyone downstream reads that as evidence. Exit 1 means the "
            "gate did NOT catch its mutation — or that nobody has registered one, "
            "which is the same problem earlier. Exit 3 on a HUMAN-approval gate: there "
            "is no command to mutate and no test could show that a person's judgement "
            "can go the other way, so nothing is broken and nothing is proven.\n\n"
            "A mutation whose `old` text is absent or ambiguous is a FAILURE, not a "
            "skip: the edit never happened, so the gate ran on pristine source and "
            "passing proves the opposite of what it claims."
        ),
        "properties": {
            "id": ("string", "Item whose worktree to mutate in.", True),
            "gate": ("string", "Gate id. Must be a command gate.", True),
        },
        "argv": lambda a: ["--json", "gate", "verify", a["id"], a["gate"]],
    },
    "ddflow_gate_skip": {
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
            "force": (
                "boolean",
                "Complete over unmet conditions. Every one is recorded in the event "
                "log as overridden, so this is visible forever rather than being the "
                "quiet way past a gate. Prefer `ddflow_gate_skip` with a reason: it "
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
    "ddflow_merge": {
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
    "ddflow_split": {
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
                "theirs with ddflow_update.",
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
    "ddflow_lesson_search": {
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
    "ddflow_bug_fixed": {
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
    "ddflow_board": {
        "description": "The whole work queue as a readable board, with the critical path.",
        "properties": {"phase": ("string", "Restrict to one phase.", False)},
        "argv": lambda a: ["board", *_opt("--phase", a)],
    },
    "ddflow_progress": {
        "description": (
            "What work has ACTUALLY been done, aggregated from the event log: attempts "
            "per item, wall-clock held, gate runs, commits produced, and who did them. "
            "Use it to answer 'how much effort has gone into this' and to see an item's "
            "full gate history including the outcomes that were not passes."
        ),
        "properties": {"id": ("string", "One item, with its per-attempt detail.", False)},
        "argv": lambda a: ["--json", "progress", *([a["id"]] if a.get("id") else [])],
    },
    "ddflow_loops": {
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
        "api": lambda repo, a, agent: _api().loops(repo),
        # The pre-migration body was the findings ARRAY. Preserved exactly.
        "payload": "findings",
    },
    "ddflow_cleanup": {
        "description": (
            "Classify every ddflow worktree and branch: merged (safe to remove), "
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
    "ddflow_recall": {
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
    "ddflow_status": {
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
    "ddflow_identify": {
        "description": (
            "Declare WHO you are on this connection, before doing anything that writes. "
            "Call this first if more than one agent or subagent is working this "
            "repository at the same time. Every attribution in the queue depends on it: "
            "who holds a claim, who ran a gate, and whether a review was done by an "
            "agent other than the author. By default ddflow derives an identity from "
            "the working tree, which is correct for one agent per tree and WRONG, with "
            "no error, for several in the same tree — their work merges into one "
            "identity, 'what was I doing' answers with someone else's task, and a "
            "review passes independence against itself. There is no way to detect this "
            "from the outside, so it has to be declared. Pick a name that is stable for "
            "your whole session and distinct from the other agents': your role or "
            "assignment, not a random string. Idempotent; call it again to correct it."
        ),
        "properties": {
            "agent": (
                "string",
                "A short stable name for you on this connection, e.g. 'reviewer-2' "
                "or 'importer'. Letters, digits, '.', '_' and '-' only, up to 64 "
                "characters — it becomes a log filename. OMIT it to reset to the "
                "tree-derived default and be told what that is.",
                False,
            ),
        },
        "identify": True,
    },
    "ddflow_decision_add": {
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
            "sources": (
                "string",
                "Where this came from: an ADR path, a URL, a commit sha. "
                "Comma-separated. Structured, so a later audit can check the source "
                "still exists rather than parsing it out of the prose.",
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
            *_opt("--tags", a) + _opt("--sources", a),
            *_opt("--status", a),
        ],
    },
    "ddflow_decision_list": {
        "description": (
            "Every architectural decision in force. Superseded ones are "
            "hidden unless you ask for them — they are kept, never deleted, "
            "because how the architecture got here is what a rebuild needs."
        ),
        "properties": {"all": ("boolean", "Include superseded decisions.", False)},
        "argv": lambda a: ["--json", "decision", "list", *(["--all"] if a.get("all") else [])],
    },
    "ddflow_decision_applicable": {
        "description": (
            "The architectural decisions that govern a specific item's declared files. "
            "CALL THIS BEFORE IMPLEMENTING: it is how a decision reaches the person "
            "writing the code, without them having to know it exists. Returns "
            "project-wide decisions too."
        ),
        "properties": {"id": ("string", "Item id.", True)},
        "argv": lambda a: ["--json", "decision", "applicable", a["id"]],
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
    "ddflow_render": {
        "description": (
            "Regenerate the human-readable markdown views (queue, lessons, "
            "research) under docs/ddflow/."
        ),
        "properties": {
            "out": ("string", "Directory for the generated views (default: docs/ddflow).", False),
            "show": (
                "string",
                "Print ONE view instead of writing files: lessons, research, or board. "
                "This is what the ddflow:// resources are served from.",
                False,
            ),
        },
        "argv": lambda a: (
            ["render", "--show", a["show"]]
            if a.get("show")
            else ["--json", "render", *_opt("--out", a)]
        ),
    },
    "ddflow_rebuild": {
        "description": (
            "Re-derive the search index from the event log. The index is a "
            "disposable cache; this is never a data-loss operation."
        ),
        "properties": {},
        "argv": lambda a: ["--json", "rebuild"],
    },
    "ddflow_history": {
        "description": (
            "ONE timeline of everything that happened, in the order it happened: "
            "claims, releases, gates, bugs, decisions, lessons, completions. The other "
            "views answer 'what is true now'; this one answers 'how did it get like "
            "this', which is the question you have when something looks wrong.\n\n"
            "Filter with `item` for one task's whole life, `kind` for one family "
            "('gate', 'lease.acquired', 'decision,bug'), `since` for a time window. "
            "Exit 2 means nothing matched — which is an answer, not a failure."
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
        },
        "argv": lambda a: (
            ["--json", "history"]
            + (["--item", a["item"]] if a.get("item") else [])
            + (["--kind", a["kind"]] if a.get("kind") else [])
            + (["--since", a["since"]] if a.get("since") else [])
            + (["--limit", str(a["limit"])] if a.get("limit") else [])
        ),
    },
    "ddflow_import": {
        "description": (
            "For a project that ALREADY HAS HISTORY and is adopting ddflow now: read "
            "its todo checklists, lessons corpus, ADR files and unmerged branches, and "
            "propose them as queue items. Reports by default and writes NOTHING until "
            "`apply` is true.\n\n"
            "Call this right after `ddflow_setup` on any repository that is not brand "
            "new. A queue that starts empty tells you nothing is in flight about a "
            "project that may have three branches in flight.\n\n"
            "The proposal is a GUESS about structure — headings became phases, "
            "checkboxes became tasks, and almost nothing has globs. Use the "
            "`import-existing-project` prompt, which walks through fixing that with the "
            "operator. Exit 2 means nothing was found."
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
        "argv": lambda a: (
            ["--json", "import"]
            + (["--apply"] if a.get("apply") else [])
            + (["--include-done"] if a.get("include_done") else [])
            + (["--max-tasks", str(a["max_tasks"])] if a.get("max_tasks") else [])
        ),
    },
    "ddflow_workflow": {
        "description": (
            "The rules THIS project runs by, in one answer: the gates every task and "
            "phase passes through in order, which are commands and which you perform "
            "yourself, which are required, which need evidence, which need a "
            "different-family reviewer, which have been PROVEN able to fail — plus "
            "the completion rules, the parallelism caps, the reviewers, and where each "
            "value came from (a default, this project's config, or the environment).\n\n"
            "Call it before your first `ddflow_claim` in a session, and after any "
            "workflow change: the connection instructions are computed once when the "
            "server starts, so a pipeline edited mid-session is not reflected there.\n\n"
            "Exit 1 means the workflow does not hang together — most importantly a "
            "pipeline naming a gate that has no definition, which blocks every item "
            "that reaches it forever. Read-only."
        ),
        "properties": {},
        "argv": lambda a: ["--json", "workflow"],
    },
    "ddflow_workflow_pipeline": {
        "description": (
            "Set the ordered list of gates a task or a phase must pass. WRITES to "
            "this project's config.\n\n"
            "Validated before anything is written: a gate id with no definition is "
            "REFUSED and the error names the near miss, because an undefined gate in a "
            "pipeline blocks every item that reaches it and cannot be recorded or "
            "skipped. Define the gate first with `ddflow_workflow_gate`.\n\n"
            "Ask the operator before changing a pipeline. It governs every future item, "
            "not the one you are working on, and removing a gate removes a check "
            "somebody added deliberately. Use dry_run to show them what it would do."
        ),
        "properties": {
            "which": ("string", "'task' or 'phase'.", True),
            "gates": ("string", "Comma-separated gate ids, in the order they run.", True),
            "dry_run": ("boolean", "Report the change and write nothing.", False),
        },
        "argv": lambda a: (
            ["--json", "workflow", "pipeline", a["which"], a["gates"]]
            + (["--dry-run"] if a.get("dry_run") else [])
        ),
    },
    "ddflow_workflow_gate": {
        "description": (
            "Define or change one gate, and optionally put it in a pipeline. WRITES to "
            "this project's config.\n\n"
            "`command` makes it a COMMAND gate: ddflow runs it and the exit code is "
            "the evidence. `prompt` makes it an AGENT gate: you perform it and record "
            "what you did. A gate needs one of the two — one with neither tells an "
            "agent nothing and gives a reviewer no contract, so it is refused.\n\n"
            "`into` adds it to a pipeline (`after` places it; default is last). "
            "`required` means an item cannot complete without it.\n\n"
            "Ask the operator first, and prefer dry_run to show them the change."
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
        "argv": lambda a: (
            ["--json", "workflow", "gate", a["id"]]
            + _opt("--command", a)
            + _opt("--prompt", a)
            + _opt("--title", a)
            + _opt("--cwd", a)
            + _opt("--reviewer", a)
            + (["--timeout", str(a["timeout"])] if a.get("timeout") else [])
            + _opt("--applies-to", a, "applies_to")
            + _opt("--into", a)
            + _opt("--after", a)
            + (["--required"] if a.get("required") else [])
            + (["--dry-run"] if a.get("dry_run") else [])
        ),
    },
    "ddflow_workflow_drop": {
        "description": (
            "Take a gate out of both pipelines, and out of `required` so it does not "
            "become a requirement that quietly requires nothing. WRITES to config.\n\n"
            "The gate's DEFINITION is left in place, so putting it back is one call. "
            "Exit 2 means it was in neither pipeline.\n\n"
            "Ask the operator first: a gate in a pipeline is a check somebody added on "
            "purpose, and removing it weakens every future item."
        ),
        "properties": {
            "id": ("string", "The gate id to remove from the pipelines.", True),
            "dry_run": ("boolean", "Report the change and write nothing.", False),
        },
        "argv": lambda a: (
            ["--json", "workflow", "drop", a["id"]] + (["--dry-run"] if a.get("dry_run") else [])
        ),
    },
    "ddflow_help": {
        "description": (
            "What ddflow IS, what it can do, and what the workflow is. Call this "
            "first if you have not used it before — the other tool descriptions "
            "explain one tool each to someone who already knows which to pick, and "
            "the connection instructions describe THIS repository right now. Neither "
            "answers 'how am I meant to work here'.\n\n"
            "With no argument: the loop from picking work to landing it, what the exit "
            "codes mean, and every capability grouped by what it is for. With a "
            "`topic`: workflow, import, gates, parallel, memory, recovery, config.\n\n"
            "Read-only. The pages are templates a project can override, so what this "
            "returns may be this project's own instructions rather than the defaults."
        ),
        "properties": {
            "topic": (
                "string",
                "workflow | import | gates | parallel | memory | recovery | config. "
                "Omit for the overview, which lists them.",
                False,
            ),
        },
        "argv": lambda a: ["--json", "help"] + ([a["topic"]] if a.get("topic") else []),
    },
    "ddflow_import_verify": {
        "description": (
            "Was this project's history imported, is that import still true, and did "
            "anyone FINISH it? Read-only; writes nothing.\n\n"
            "Three answers in one call. STATUS: how many phases, tasks, branches, "
            "lessons, decisions, research notes, journal entries and memories carry "
            "import provenance, and when. STILL TRUE: whether the source files have "
            "moved on since (and what a re-run would add), and whether any imported "
            "item names a source file that no longer exists. FINISHED: the half the "
            "`import-existing-project` prompt asks a human for and nothing else "
            "checks — imported tasks with no globs, which the conflict detector "
            "cannot protect, and phases whose heading claims the work shipped while a "
            "task under them is still open.\n\n"
            "Call it after any import, and whenever you are about to hand out imported "
            "work. Exit 1 means findings you should put to the operator; exit 2 means "
            "nothing was ever imported, which is an answer, not a failure. It does not "
            "repeat what `ddflow_doctor` covers — unresolved dependencies, duplicate "
            "globs, cycles — so run that too."
        ),
        "properties": {},
        "argv": lambda a: ["--json", "import", "--verify"],
    },
    "ddflow_companions": {
        "description": (
            "Which companion MCP servers serve this project's gates, which are "
            "installed on this machine, and which are wired into an agent's config. "
            "ddflow imposes the pipeline; it does not perform the judgement inside "
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
    "ddflow_companions_add": {
        "description": (
            "WRITES to this repository's agent config. Registers companion MCP "
            "servers that are ALREADY installed, merging rather than overwriting what "
            "is there. Refuses (exit 3) to register one that is not installed, because "
            "that writes a launch command which fails mid-task, at the moment a gate "
            "told the agent to reach for it. "
            "Call it with dry_run=true FIRST, show the operator the exact entry it "
            "reports, and write only once they agree: which servers an agent launches "
            "is the operator's decision, not yours."
        ),
        "properties": {
            "id": ("string", "Comma-separated ids; default: every installed one.", False),
            "agents": ("string", "Comma-separated agent keys (default: claude).", False),
            "dry_run": (
                "boolean",
                "Report the exact config entry that would be written, and write "
                "nothing. Use this first, and show the operator the result.",
                False,
            ),
        },
        "argv": lambda a: (
            ["--json", "companions", "add"]
            + (["--id", a["id"]] if a.get("id") else [])
            + (["--agents", a["agents"]] if a.get("agents") else [])
            + (["--dry-run"] if a.get("dry_run") else [])
        ),
    },
    "ddflow_bug_found": {
        "description": (
            "Report a bug the moment you find it, BEFORE fixing it. Recording it first "
            "is what makes the fix accountable: `ddflow_bug_fixed` refuses to close "
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
    "ddflow_session_note": {
        "description": (
            "Record something that happened during a session which is neither an "
            "operator prompt nor a decision — a surprise, a dead end, why you changed "
            "approach. It goes into the reconstruction alongside the prompts, and a "
            "dead end recorded is a dead end nobody walks down twice."
        ),
        "properties": {
            "session": ("string", "Session id from ddflow_session_start.", True),
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
        "argv": lambda a: [
            "--json",
            "session",
            "end",
            a.get("session", ""),
            *_opt("--summary", a),
        ],
    },
    "ddflow_decision_show": {
        "description": (
            "Read ONE architectural decision in full — its context, what was decided, "
            "the consequences, and what was rejected. `ddflow_decision_list` gives "
            "you the titles; this is what you read before working against one, and "
            "especially before proposing something it already considered."
        ),
        "properties": {"id": ("string", "Decision id.", True)},
        "argv": lambda a: ["--json", "decision", "show", a["id"]],
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
        "argv": lambda a: (
            ["--json", "prompts", "list"]
            if a.get("action", "list") == "list"
            else ["prompts", a["action"], *([a["name"]] if a.get("name") else [])]
        ),
    },
    "ddflow_hooks": {
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
    "ddflow_doctor": {
        "description": (
            "Integrity and health check: log corruption, dependency cycles, "
            "unknown dependencies, orphaned worktrees, stale index."
        ),
        "properties": {},
        "argv": lambda a: ["doctor"],
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
        "argv": lambda a: ["--json", "cadence", *_opt("--ran", a), *_opt("--note", a)],
    },
    "ddflow_session_prompt": {
        "description": (
            "Record the operator's prompt verbatim. This is what makes the project "
            "reconstructible from the log alone if everything else is lost. Secrets are "
            "redacted before anything touches disk. Call it once per operator turn."
        ),
        "properties": {
            "session": ("string", "Session id from ddflow_session_start.", True),
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
                "Comma-separated agents to write driver deltas for: "
                "claude,gemini,codex,copilot,kilo,cursor. Default: all.",
                False,
            )
        },
        "argv": lambda a: ["adopt", *_opt("--agents", a)],
    },
    "ddflow_configure": {
        "description": (
            "Read or write .ddflow/config.toml. With no arguments it prints every "
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
    "ddflow_reviewers_detect": {
        "description": (
            "Probe well-known local ports for an OpenAI-compatible model server (ollama, "
            "vLLM, LM Studio, llama.cpp, sglang) and report what is serving, with each "
            "model's pretraining family. Use this to find a reviewer from a DIFFERENT "
            "family than yourself — which the critic gate requires. Pass write=true to "
            "add what it finds to .ddflow/config.toml."
        ),
        "properties": {
            "write": ("boolean", "Append the discovered reviewers to the config.", False)
        },
        "argv": lambda a: ["reviewers", "detect"] + (["--write"] if a.get("write") else []),
    },
    "ddflow_reviewers_list": {
        "description": "Show the configured reviewers, their families and which gates they serve.",
        "properties": {},
        "argv": lambda a: ["reviewers", "list"],
    },
    "ddflow_review": {
        "description": (
            "Run the configured cross-family reviewer over an item's diff and record the "
            "result. This is the critic gate performed by ddflow rather than claimed by "
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
    "ddflow_show": {
        "description": (
            "Everything known about one phase or task: state, dependencies, declared "
            "globs, the lease and who holds it, the worktree path you can cd to, and "
            "every gate's outcome with its evidence. Use it to check your own work "
            "before calling ddflow_complete."
        ),
        "properties": {"id": ("string", "Item id.", True)},
        "argv": lambda a: ["--json", "show", a["id"]],
    },
    "ddflow_update": {
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
            agent=agent,
            title=a.get("title"),
            body=a.get("body"),
            needs=_list_or_none(a, "needs"),
            globs=_list_or_none(a, "globs"),
            tags=_list_or_none(a, "tags"),
            priority=a.get("priority"),
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
        "argv": lambda a: ["--json", "release", a["id"], *_opt("--note", a)],
    },
    "ddflow_block": {
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
    "ddflow_session_start": {
        "description": "Open a session for provenance logging. Returns the session id.",
        "properties": {
            "model": ("string", "Your model id.", False),
            "tool": ("string", "Your harness, e.g. 'claude-code'.", False),
        },
        "argv": lambda a: ["--json", "session", "start", *_opt("--model", a), *_opt("--tool", a)],
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


def _api():
    """Imported lazily: `surfaces` may reach `api`, and doing it at call time keeps the
    module import graph flat for anything that only wants the tool table."""
    from .. import api

    return api


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


def _outcome_result(out: Any, payload_key: str = "") -> dict[str, Any]:
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
    data = out.data.get(payload_key) if payload_key else out.data
    body = json.dumps(data, indent=2, default=str)
    if out.reason:
        body = f"{out.reason}\n\n{body}"
    return _text(body, error=(out.exit == 1), meta={"exit": out.exit})


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


def _run_cli(
    repo: Path, argv: list[str], agent: str = "", called_from: Path | None = None
) -> tuple[int, str]:
    """Invoke the CLI in-process, capturing both streams.

    In-process rather than subprocess: it is ~40x faster per call, and it guarantees
    the MCP surface and the shell surface execute literally the same code, which is the
    property that stops them drifting.
    """
    out, err = io.StringIO(), io.StringIO()
    real_out, real_err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = out, err
    # Imported HERE, not at module scope. `cli` imports this module for the help
    # inventory and this module imports `cli` to run it -- a cycle held apart only by
    # both edges being function-local, which nothing checked until
    # `test_no_module_level_import_cycles`. The edge disappears entirely when the last
    # tool leaves the argv path: `_run_cli` is the only thing that needs `cli_main`.
    from .cli import main as cli_main

    # `--agent` BEFORE the subcommand: it is a top-level flag, and argparse puts a
    # top-level flag appearing after the subcommand name into the subparser, where it
    # does not exist. An identity silently dropped is worse than one never set -- the
    # events would be attributed to the process default and look entirely plausible.
    # `--repo` gets the CALLER's location, not the resolved primary. `Ctx` resolves the
    # primary itself for the log and config, and keeps the unresolved path for the one
    # decision that needs it. Passing the primary here is what made adoption a CLI-only
    # feature.
    where = called_from or repo
    head = ["--repo", str(where)] + (["--agent", agent] if agent else [])
    try:
        code = cli_main([*head, *argv])
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
    `agent` argument on the individual call. Explicit beats derived, innermost wins.
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
        #: What the client called itself at `initialize`. A LABEL, never an identity:
        #: every subagent of one harness reports the same `clientInfo.name`, so using
        #: it as an id would reproduce the exact collapse above while looking specific.
        self.client_info: dict[str, Any] = {}

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
                    "instructions": _instructions(self.repo, self.agent),
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
                if want:
                    detail = "declared on this connection"
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
                return _ok(
                    mid,
                    _text(
                        f"identified as {who!r} ({detail}). Claims, gate outcomes and "
                        f"reviews on this connection are attributed to it.{note}"
                    ),
                )
            if "api" in spec:
                try:
                    result = spec["api"](self.repo, args, self.agent)
                except (KeyError, TypeError, ValueError) as exc:
                    return _ok(mid, _text(f"bad arguments: {exc}", error=True))
                return _ok(mid, _outcome_result(result, spec.get("payload", "")))

            try:
                argv = spec["argv"](args)
            except (KeyError, TypeError) as exc:
                return _ok(mid, _text(f"bad arguments: {exc}", error=True))
            code, body = _run_cli(self.repo, argv, self.agent, self.called_from)
            # Exit 2 ("nothing to do") and 3 ("coordination refused") are RESULTS, not
            # errors: the model must read and act on them. Only 1 is a genuine failure.
            return _ok(mid, _text(body or f"(exit {code})", error=(code == 1), meta={"exit": code}))
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
                "ddflow://board": ["board"],
                "ddflow://brief": ["brief"],
                "ddflow://lessons": ["render", "--show", "lessons"],
                "ddflow://research": ["render", "--show", "research"],
            }.get(uri)
            if not cmd:
                return _err(mid, -32602, f"unknown resource {uri!r}")
            _, body = _run_cli(self.repo, cmd)
            return _ok(mid, {"contents": [{"uri": uri, "mimeType": "text/markdown", "text": body}]})
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
                        for name, (title, desc, args) in sorted(P.COMMANDS.items())
                    ]
                },
            )
        if method == "prompts/get":
            from ..services import prompts as P

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
        "gate_gaps": [],
        "recoverable": 0,
        "ready": 0,
        "running": 0,
        "blocked": 0,
        "open_bugs": 0,
        "loops": 0,
        "task_pipeline": [],
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
        from ..services import importer as IM

        v["importable"] = len(IM._files(repo, IM.SOURCE_GLOBS))
    except Exception:
        pass
    if not adopted:
        return v

    try:
        from ..config import Config

        cfg = Config.load(repo)
    except Exception:
        return v
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
                "server if one is running."
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
        word = {
            "register": "installed, not registered",
            "install": "not installed",
            "check": "not checked",
        }
        v["actionable_companions"] = [
            {**c, "state_word": word[c["advice"]]}
            for c in by["register"] + by["install"] + by["check"]
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
        log = EventLog(repo, effective_agent_id(repo, cfg, agent))
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
        v["recoverable"] = len(L.scan(log, cfg, repo))
    except Exception:
        pass
    return v


def _instructions(repo: Path, agent: str = "") -> str:
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


def serve(repo: Path, stdin=None, stdout=None, *, called_from: Path | None = None) -> None:
    """Newline-delimited JSON-RPC over stdio, until EOF.

    Nothing may be written to stdout except protocol frames — a stray print corrupts
    the stream and the client sees a hung server. Diagnostics go to stderr.
    """
    srv = Server(repo, called_from=called_from)
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
