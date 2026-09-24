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
        },
        "argv": lambda a: ["brief", *_opt("--item", a), *_opt("--phase", a)],
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
        },
        "argv": lambda a: ["--json", "claim", a["id"], *_opt("--globs", a), *_opt("--note", a)],
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
        "argv": lambda a: ["gate", "run", a["id"], a["gate"]],
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
            ]
        ),
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
        },
        "argv": lambda a: ["--json", "complete", a["id"], *_opt("--sha", a), *_opt("--model", a)],
    },
    "orchard_merge": {
        "description": (
            "Merge an item's branch into the base branch from the primary "
            "checkout, without ever switching its branch."
        ),
        "properties": {
            "id": ("string", "Item id.", True),
            "message": ("string", "Merge commit message.", False),
        },
        "argv": lambda a: ["--json", "merge", a["id"], *_opt("--message", a)],
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
            "body": ("string", "Detail, acceptance criteria, context.", False),
        },
        "argv": lambda a: (
            ["phase", "add", a["id"], *_opt("--title", a), *_opt("--needs", a), *_opt("--body", a)]
        ),
    },
    "orchard_task_add": {
        "description": (
            "Add a task to a phase. ALWAYS set globs to the paths this task will write: "
            "they are what lets two agents work in parallel safely, and an unset glob "
            "means the conflict detector cannot protect you."
        ),
        "properties": {
            "id": ("string", "Short stable id, e.g. 'P2.T1'.", True),
            "phase": ("string", "Owning phase id.", True),
            "title": ("string", "One-line description.", False),
            "needs": ("string", "Comma-separated ids this task depends on.", False),
            "globs": ("string", "Comma-separated path globs this task writes.", False),
            "body": ("string", "Detail and acceptance criteria.", False),
        },
        "argv": lambda a: (
            [
                "task",
                "add",
                a["id"],
                "--phase",
                a.get("phase", ""),
                *_opt("--title", a),
                *_opt("--needs", a),
                *_opt("--globs", a),
                *_opt("--body", a),
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
            "title": ("string", "The rule as a one-line statement.", True),
            "rule": ("string", "The rule in full.", False),
            "why": ("string", "Why it is true / what went wrong.", False),
            "how": ("string", "How to apply or detect it.", False),
            "tags": ("string", "Comma-separated tags.", False),
        },
        "argv": lambda a: (
            [
                "lesson",
                "add",
                "--title",
                a.get("title", ""),
                *_opt("--rule", a),
                *_opt("--why", a),
                *_opt("--how", a),
                *_opt("--tags", a),
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
            "question": ("string", "What was asked.", True),
            "verdict": ("string", "CONFIRMED | REFUTED | THEORETICAL", True),
            "claim": ("string", "The falsifiable claim.", False),
            "falsifier": ("string", "The single observation that would kill it.", False),
            "probe": ("string", "The command you ran.", False),
            "probe_output": ("string", "Its output, verbatim.", False),
            "sources": ("string", "Comma-separated URLs/DOIs you actually opened.", False),
        },
        "argv": lambda a: (
            [
                "--json",
                "research",
                "--question",
                a.get("question", ""),
                "--verdict",
                a.get("verdict", "THEORETICAL"),
                *_opt("--claim", a),
                *_opt("--falsifier", a),
                *_opt("--probe", a),
                *_opt("--probe-output", a, "probe_output"),
                *_opt("--sources", a),
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
        "properties": {"item": ("string", "Restrict to one item.", False)},
        "argv": lambda a: ["--json", "recover", *_opt("--item", a)],
    },
    "orchard_board": {
        "description": "The whole work queue as a readable board, with the critical path.",
        "properties": {"phase": ("string", "Restrict to one phase.", False)},
        "argv": lambda a: ["board", *_opt("--phase", a)],
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
        "properties": {"ran": ("string", "Record that this cadence just ran.", False)},
        "argv": lambda a: ["--json", "cadence", *_opt("--ran", a)],
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
        },
        "argv": lambda a: ["review", a["id"], *_opt("--gate", a), *_opt("--intent", a)],
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


def _opt(flag: str, args: dict[str, Any], key: str | None = None) -> list[str]:
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
            cmd = {
                "orchard://board": ["board"],
                "orchard://brief": ["brief"],
                "orchard://lessons": ["render", "--out", "/dev/null"],
            }.get(uri)
            if uri in {"orchard://lessons", "orchard://research"}:
                from . import render as R
                from .events import EventLog
                from .model import fold

                st = fold(EventLog(self.repo).read_all(), strict=False)
                body = R.lessons_md(st) if uri.endswith("lessons") else R.research_md(st)
            elif cmd:
                _, body = _run_cli(self.repo, cmd)
            else:
                return _err(mid, -32602, f"unknown resource {uri!r}")
            return _ok(mid, {"contents": [{"uri": uri, "mimeType": "text/markdown", "text": body}]})
        if method == "prompts/list":
            return _ok(mid, {"prompts": []})
        return _err(mid, -32601, f"method not found: {method}")


def _instructions(repo: Path) -> str:
    """What the client injects into the model's context on connect.

    **State-aware on purpose.** A fixed blurb describing a workflow the project has not
    adopted is noise the model learns to skip; the useful instruction is the next
    concrete action, and that depends on whether `.orchard/` exists, whether a test
    command is set, whether a cross-family reviewer is configured, and whether anything
    is waiting to be recovered. This is the only place the server gets to speak
    unprompted, so it says the one thing that is true right now.
    """
    orchard_dir = repo / ".orchard"
    if not orchard_dir.is_dir():
        return (
            "This repository does not use Orchard yet.\n\n"
            "If the user wants a managed work queue — phases and tasks with "
            "dependencies, parallel agents in isolated git worktrees, quality gates and "
            "crash recovery — call `orchard_setup` ONCE. It creates .orchard/, writes "
            "the driver, and adds a short section to AGENTS.md describing how work is "
            "claimed here. Then set the project's test command with `orchard_configure` "
            "and add work with `orchard_phase_add` / `orchard_task_add`.\n\n"
            "Do not call the other tools before `orchard_setup`; they will report that "
            "there is no queue.\n\n"
            "If the user has not asked for this, say nothing about it and carry on."
        )

    lines = [
        "This repository's work is a queue managed by Orchard.",
        "",
        "**Call `orchard_brief` first.** It returns any work left over from a crashed "
        "agent, what is ready to start now, why everything else is blocked, and the "
        "past lessons relevant to the task — and it replaces reading this project's "
        "rule and lesson files.",
        "",
        "**Claim before you edit.** `orchard_claim` leases an item and gives you an "
        "isolated git worktree. An unclaimed edit can be destroyed by a parallel agent, "
        "and in this repository it may also be refused at commit time.",
        "",
        "Loop: `orchard_next` → `orchard_claim` → work in the worktree → "
        "`orchard_gate_status` and satisfy each gate → `orchard_merge` → "
        "`orchard_complete`.",
        "",
        "A tool or reviewer that could not run is recorded `unavailable`, NEVER "
        "`passed`. Exit code 2 means 'could not run / nothing to do' and is a result, "
        "not an error — never treat it as success.",
    ]

    # Name the specific setup gaps, because "configure it properly" is not actionable
    # and an agent cannot see the config file's contents from here.
    todo = []
    try:
        from .config import Config
        from .gates import load_gates
        from .reviewer import load_reviewers

        cfg = Config.load(repo)
        gates = load_gates(repo, cfg)
        ut = gates.get("unit_tests")
        if not ut or not ut.command or "set [gate.unit_tests]" in ut.command:
            todo.append(
                "No test command is configured. Set it with `orchard_configure`: "
                '`[gate.unit_tests]` / `command = "<your test command>"`. Until then '
                "the unit_tests gate reports UNAVAILABLE and cannot pass."
            )
        if not load_reviewers(repo):
            todo.append(
                "No cross-family reviewer is configured, so the `critic` gate cannot "
                "run and `orchard_complete` will refuse. Call "
                "`orchard_reviewers_detect` with write=true — it finds a local model "
                "server if one is running."
            )
    except Exception:
        pass
    if todo:
        lines += ["", "**Setup still needed:**", ""] + [f"- {t}" for t in todo]
    return "\n".join(lines)


def _ok(mid: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def _err(mid: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def _text(body: str, *, error: bool = False, meta: dict | None = None) -> dict[str, Any]:
    res: dict[str, Any] = {"content": [{"type": "text", "text": body}], "isError": error}
    if meta:
        res["_meta"] = meta
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
