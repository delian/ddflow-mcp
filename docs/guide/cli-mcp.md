# The CLI and MCP surfaces

The standalone, MCP and AGENTS.md surfaces, and the command reference. Part of the ddflow [README](../../README.md); the reference moved out of it by D-readme-split. Every heading here keeps the anchor it had in the README.

## Two ways to drive it

The CLI is the whole product. The MCP server is a second surface over the same
commands, and `tests/test_mcp_parity.py` fails if the two diverge — every subcommand has
a tool, every flag is reachable, and each exemption carries a written reason.

### Standalone: a terminal, a Makefile, CI

```console
$ ddflow init
$ ddflow config --set gate.unit_tests.command "python -m pytest -q"
$ ddflow phase add P1 --title "Billing" --globs "src/billing/**"
$ ddflow task add P1.T1 --phase P1 --title "Tax rules" --globs "src/billing/tax.py"

$ ddflow next                          # exit 2 = nothing actionable; exit 1 = unknown --phase
$ ddflow claim P1.T1                   # exit 3 = refused, with the reason
leased P1.T1 · worktree .ddflow/worktrees/P1.T1 · branch ddflow/P1.T1

$ cd .ddflow/worktrees/P1.T1 && ...   # do the work
$ ddflow gate status P1.T1             # what the pipeline wants next
$ ddflow gate run P1.T1 unit_tests     # runs it; the exit code IS the evidence
$ ddflow gate record P1.T1 implement --outcome passed --evidence "added tax.py"
                                       # failed/unavailable/partial/skipped also need --reason
$ ddflow complete P1.T1                # exit 3 lists whatever is unsatisfied
$ ddflow merge P1.T1
```

You get everything except the judgement. Command gates run themselves; agent gates wait
for a human to record an outcome, and `ddflow gate skip <id> <gate> --reason "..."` is
the escape hatch — recorded as a skip, never as a pass.

**Merge queue.** Where several agents land work at once, `ddflow queue add <id>` enqueues an item,
`ddflow queue run` (any agent may) integrates the head of the queue — one entry's ci at a time,
`--max-batch N` entries per run, `--unit-tests` / `--no-unit-tests` to run or skip each entry's
`unit_tests` gate on the merged result — `ddflow queue status` (the default) prints the line, and
`ddflow queue remove <id>` drops an entry. Defaults live in `[merge_queue]`.

**Changelog line (optional).** `ddflow complete P1.T1 --changelog "Added: tax rounding"` (and
`ddflow bug fixed B1 --regression-test ... --changelog "Security: ..."`, MCP `changelog` on
`ddflow_complete` / `ddflow_bug_fixed`) records one line for the project's changelog on the
completion event. The category is one of Added, Changed, Deprecated, Removed, Fixed, Security
(case-insensitive; anything else is refused with that list); `--changelog skip` (or `internal`)
marks work that must not appear in the changelog. `complete` never asks for it and never
requires it, and logs without it fold exactly as before.

In CI, the exit codes are the interface:

```make
check:
	ddflow doctor        # 1 = integrity problems, each named
	ddflow workflow      # 1 = the pipeline does not hang together
	ddflow cadence       # 2 = no periodic pass is due
```

`2` is never "no problem". A job that treats it as success reports a green build for a
suite that never ran.

### As an MCP server

`ddflow mcp` speaks newline-delimited JSON-RPC over stdio, and treats the directory it was started in as the caller's location (as `ddflow-mcp` does), so a merge from inside a worktree keeps that worktree. You rarely run it by hand —
`ddflow adopt` writes the launch entry into each agent's own config and leaves existing
servers alone:

**22 agents are supported.** The full table, with what each one gets, is in
[Wiring it into your agent](../../adoption.md#wiring-it-into-your-agent).

It also copies the driver to `docs/ddflow/drivers/`, and installs the pre-commit hook
that enforces claim-before-you-edit.

**What an agent sees the moment it connects**, with no call to make:

- **Instructions**, returned inside the `initialize` result itself — and state-aware:
  what is ready, what is in flight, which setup is missing, whether this project has
  history worth importing, whether an import was left unfinished.
- **Tools** — one per CLI command.
- **Resources** — `ddflow://board`, `ddflow://brief`, `ddflow://lessons`,
  `ddflow://research`.
- **Prompts** — which a client turns into slash commands. **Tools are things an agent
  calls; prompts are things you invoke.**

**A refused call says so first.** Over MCP a tool that did not do what was asked — a claim
refused for an overlap (exit 3), or any call whose result would otherwise be its success
shape in nulls — returns a JSON body whose first key is
`"refusal": {"reason": ..., "outcome": ..., "exit": ...}`, followed by whatever the
operation actually said (a refused claim's `alternatives`). The lead is named `refusal` for
any call that did not do what was asked: `outcome` (`failed`, `nothing` or `refused`) and
`exit` say which, so an exit-1 failure is not mistaken for a refusal. A tool whose body is one
field (`ddflow_decision_show` on an unknown id) leads with the same object instead of a bare
`null`. Exit 2 ("nothing") keeps its declared keys after that lead, and any other data it
carried; a data field that is itself named `refusal` is kept as `refusal_data`. A result that
fills its declared shape is left as the CLI's `--json` prints it, with the reason in the
second content block.

Two tools exist so an agent can orient itself without being told: `ddflow_help` (what
is this, what is the loop) and `ddflow_workflow` (what are the rules *here*).

**Protocol versions: both eras.** A client that opens with `initialize` negotiates
`2025-11-25`, `2025-06-18`, `2025-03-26` or `2024-11-05` (an unknown version falls back to
the newest) and is served exactly as before; `2025-11-25` adds nothing a tools, resources
and prompts server must do differently -- a bad argument was already a tool result with
`isError`, never a protocol error. A client on the stateless `2026-07-28` revision sends no
`initialize`: each request carries `io.modelcontextprotocol/protocolVersion` and
`io.modelcontextprotocol/clientCapabilities` in `params._meta`, and the server answers it
on its own. `server/discover` returns the supported versions, capabilities and the same
instructions `initialize` carries; a version not served per request is refused with
`-32022` (`data.supported` lists every version), a missing required `_meta` field with
`-32602`; every modern result carries `resultType: "complete"` and the server's identity
in `_meta`, and `tools/list`, `resources/list`, `resources/read`, `prompts/list` and
`server/discover` carry `ttlMs` and `cacheScope: "private"` (an hour for the two lists
fixed for the life of the server, 0 for everything read from the repository's state).
Identity is per request in that era (D-mcp-identity-per-call): a modern request names its
caller with the `as_agent` argument or `ddflow/agent` in `params._meta` (the argument wins
when both are given, and `_meta` is then not read; a malformed name that is read is a tool
error), and one that names nobody is
attributed to the tree-derived default. Like `as_agent`, a `_meta` name other than the
connection's own does not adopt the tree the server stands in on `ddflow_claim` (it could
be a subagent riding the connection): the item gets a tree of its own, as from the CLI in
the primary. `ddflow_identify` on a modern request persists
nothing for the connection and answers how to name yourself instead; a connection that
opened with `initialize` keeps connection-wide `ddflow_identify` exactly as before, with
`as_agent` as the per-call form.

One module, `ddflow/surfaces/mcp_protocol.py`, holds everything that differs between
revisions; no tool sees which one a request came in under. It also carries the
`2026-07-28` multi round-trip: a `tools/call`, `resources/read` or `prompts/get` may be
answered `resultType: "input_required"` with `inputRequests` (only the kinds the client
declared in its capabilities) and a sealed `requestState` that the client echoes on its
retry. The state is HMAC-bound to the method, the request's name/arguments/uri, the
`ddflow/agent` caller and a 10-minute expiry, for the life of the server process. A retry
whose `requestState` is forged, tampered with, presented on another request or expired is
refused with `-32602`. No tool asks for input yet. CI replays every reply against the
official `schema.json` of each revision served (`tests/fixtures/mcp-schema/`, the upstream
files pinned by commit and sha256).

### What goes in AGENTS.md / CLAUDE.md

`ddflow adopt` writes it as a managed block between `<!-- ddflow:begin rules/work-queue
ddflow=V fmt=N sha=H -->` and `<!-- ddflow:end rules/work-queue -->` (the version that wrote it,
its format level and a digest of the body, so an older, newer or hand-edited block is told
apart). Your own prose around it is preserved; re-running updates only what is inside. A block
under the `DDFLOW:BEGIN`/`DDFLOW:END` markers older ddflow wrote is read as before and moved to
the new ones the next time it is written; a block a newer ddflow wrote is refused (exit 3,
"upgrade ddflow to >= X") instead of rewritten, and `ddflow adopt --refresh-docs` copies the
files it is about to rewrite to `.ddflow/backups/` first. The `/implement` command file is a
region of the same kind (`commands/implement`); delete its begin line to keep your own. If you
write it by hand, four things have to be in it:

1. **Start every session with `ddflow_brief`** (or `ddflow brief` in a shell).
2. **Claim before you edit** — `ddflow_next` → `ddflow_claim` → work in the worktree
   it creates.
3. **The loop** — `ddflow_gate_status` → satisfy each gate → `ddflow_complete` →
   `ddflow_merge`.
4. **The exit codes**, and that `2` is not success.

Without that block an agent sees the tools and has no reason to reach for them before
editing. The block is what makes the queue authoritative rather than optional — and it
is 232 words, because an instruction file nobody finishes reading is one nobody follows.

---

## Keeping the two surfaces honest

Every CLI command is reachable over MCP — that is the point of the tool list, and it is
the requirement that an operator in a chat window, possibly driving a remote agent, can
do everything a shell can. Three ratchets keep it true, and each one was added after the
previous one turned out to be too shallow:

| Ratchet | What it caught on its first run |
|---|---|
| every CLI **command** has a tool | the original check |
| every CLI **subcommand** has a tool | `ddflow gate skip` and `bug found` had none — `gate` counted as "covered" by `gate run`, and a parent's coverage says nothing about its children |
| every CLI **flag** is reachable from its tool | **27 divergences** — 16 on its first run, and 11 more the moment it derived its own coverage instead of using a hand-written list. Including `phase add --globs`: over MCP a phase could not declare what it writes, so the conflict detector had nothing to compare at phase level |

A fourth ratchet bounds the cost of that surface: the whole tool list is sent to the model
on every session, so `tests/test_mcp_tool_budget.py` fails if the compact `tools/list`
exceeds its byte budget (about 91 KB for the whole list, down from 119 KB) or if the shared
`as_agent` / `relation` / `check_only` descriptions are repeated at length on any tool.
Their full text lives once, in `ddflow_identify` and the handshake instructions.

**Result schemas.** `[mcp].output_schemas = "off" | "on"` (env `DDFLOW_MCP_OUTPUT_SCHEMAS`;
default `off`) makes `tools/list` declare an `outputSchema` for each tool whose result is a JSON
object of declared fields (`{"type": "object", "properties": {"schema": {"const": "claim@1"}, <its fields>: {}}}`:
additive, nothing required, so a field added within a version never invalidates a result) and
return that object also as `structuredContent` next to the text block. It needs MCP 2025-06-18
or later (an older client is served as with `off`), costs about 16 KB more `tools/list` and
sends every such result twice, which is why it is off. (A tool that answers one record or
`null` -- `show`, `decision_show`, `recall` -- or text, or what the arguments decide, declares
none.) A tool whose result is a bare array
keeps its exact shape; its schema is under the tool's `_meta["ddflow/outputSchema"]`, because an
`outputSchema` must describe an object. The `schema` tag in the result itself is there with the
knob off too (see "What `--json` and the MCP tools return has a name", near the top).

**Tool tiers.** A client that loads every tool schema up front still pays that ~91 KB, so
`[mcp].tools = "core" | "standard" | "all"` (env `DDFLOW_MCP_TOOLS`; default `all`) chooses
which tools `tools/list` advertises: `core` is 38 KB (the daily loop: brief, next,
claim, heartbeat, gates, complete, merge, status, show, recall, bugs, lessons, decisions,
sessions, identify, task add/update, wait, review, help, pr sync, similar, setup), `standard`
is 66 KB (core plus the commonly used rest), `all` is every tool, byte-identical to
before. It is a start-time choice and only about what is listed: a tool outside the tier is
**still callable by name**, `ddflow_help` and the connection instructions say what the tier
hides and how to widen it, and `listChanged` stays false, so change the knob and restart the
server. An unrecognised value is refused on write and lists everything at start. The tier
sets are one structure in `ddflow/surfaces/mcp.py` (`CORE_TOOLS`, `STANDARD_EXTRA_TOOLS`,
`FULL_ONLY_TOOLS`) and `tests/test_mcp_tool_tiers.py` fails until every new tool is placed in
exactly one. The parity ratchets read the whole registry, so they never depend on the tier.

The flag ratchet derives its own input from the parser rather than a hand-written list —
its first version carried eleven tools and was blind to `remove --force` for exactly
that reason. Omissions are allowed, but each must be a `flag_exempt` field on a command in
`ddflow/surfaces/exemptions.py` with its reason, so "we chose not to expose this" and "nobody noticed" stop looking alike.

A fourth pins something subtler: **whether a tool returns JSON or prose is a decision,
not an accident.** Some tools deliberately return prose — `brief`, `gate status` and
`replay` exist to hand the model an *instruction* or a narrative, and JSON-encoding a
paragraph so the client can decode it again helps nobody. But `decision add` returned
JSON while `task add` returned prose for no reason either could state. Each prose tool
now carries its justification as the `prose_reason` of its declaration in
`ddflow/surfaces/exemptions.py`.

## Command reference

```
ddflow adopt [--agents ...]     install into a project, for one or more agents
ddflow adopt --refresh-docs     rewrite only the driver docs + rules blocks (doctor says when)
ddflow init                     create .ddflow/ only

ddflow phase add <id> [...]     add a phase
ddflow task add <id> --phase .. add a task
ddflow update <id> [...]        change title/body/needs/globs/tags/priority (--globs REPLACES)
ddflow update <id> --worktree P rebind the item and its lease to the linked worktree at P
ddflow resolve <id> --keep X    settle a contested item: keep one definition or one claim

ddflow plan check <file|->      run plan check before filing a phase: a wildcard-directory
                                glob, a cycle, an unknown `needs`, two tasks that can run
                                together whose globs collide, or a task with no acceptance
                                (1 = findings, 0 = clean, 3 = not a plan at all)

ddflow docs check [--diff BASE] do the names, flags, links and anchors the documents mention
                                still exist? (`[docs]`: severity, check_paths, check_links,
                                lint_command; --diff keeps the surface added since BASE;
                                1 = findings, 2 = could not run, never a clean report)

ddflow next [--phase P]         what may start now       (2 = nothing actionable)
ddflow claim <id> [--globs ..]  lease + create worktree  (3 = refused)
ddflow heartbeat <id>           renew a lease
ddflow release <id>             give it up

ddflow gate status <id>         pipeline position + the next gate's instruction
ddflow gate list [--refuted [--since D]]    the defined gates; --refuted: every gate passed on refutation (spot-check), --since: recorded at or after D
ddflow gate run <id> <gate>     execute a command gate, record its evidence  (--rerun-failed: run ONLY the failed tests once more; the gate stays failed)
ddflow gate record <id> <gate>  record an agent gate    (--outcome, --reason, --model, --reviewer-model, --reviewed-sha)
ddflow gate skip <id> <gate>    skip, with a mandatory reason
ddflow approve <id> <gate>      a PERSON clears a human gate  (no MCP equivalent)
ddflow approve .. --reject      ...or refuses it, with --reason
ddflow gate verify <id> <gate>  prove the gate CAN fail  (1 = it cannot)
ddflow tests [--item <id>]      tests the change reaches + a parallel command  (2 = none); --flakes: the flake log
ddflow report <id> [--agent A]  the completion report of an item from the log (shas, tests, gates, overrides, what is open)

ddflow merge <id>               merge from the primary checkout, no checkout
ddflow merge <id> --allow-empty land a branch with nothing ahead of its target (refused otherwise)
ddflow merge <id> --branch <b>  an item claimed --no-worktree: land <b> (default: your tree's branch)
                                ([flow].integration=pr: push + open/update a PR instead)
ddflow pr sync [--item]         what reviewers did: complete / reopen / park / merge (2 = forge unreachable)
ddflow pr status               every item's request, from the log (no forge call)
ddflow pr threads <id> [--thread T [--reply TEXT] [--resolve]]  review threads, live from the forge; reply and resolve one (2 = forge unreachable)
ddflow version show            current and next version, why, release notes (2 = nothing new)
ddflow version cut [--push]    tag it (gitflow: via release/X, or a release PR)
ddflow version lint [--waive CHANGE --reason WHY]  is every knob / event-kind change announced in the upgrade manifest? (3 = not, under `[release].manifest_lint = block`)
ddflow version cut --changelog  also write the version's CHANGELOG.md section (--force over a hand-edited file)
ddflow version show|cut --line L    the same, for a maintenance line (keeps its major)
ddflow task add <id> --port-of FIX  a follow-up to FIX: takes the lines FIX reached
ddflow task add <id> --lines 1,2,3  a fix for several release lines: ports generated
ddflow promote add <env>        file a promotion one step downstream (2 = nothing to carry)
ddflow promote status           each environment: head, behind upstream, open promotion, live sha
ddflow promote deployed <env>   record the sha a deploy put live (from the deploy hook; --sha, default the head)
ddflow flow show                how this project works: model, lines, every choice + who made it
ddflow flow choose <knob> <v>   record a workflow choice, with --reason
ddflow complete <id>            finish        (3 = unmet conditions, all listed)
ddflow block <id> --reason ..   mark blocked (a DONE or ABANDONED item is refused, exit 3, unless --reopen)
ddflow abandon <id> --reason .. stop work on an item without completing it
ddflow remove <id> [--force]    take an item out of the queue (recorded, not erased)

ddflow brief [--item|--phase]   budgeted session-start pack
ddflow board / show <id>        human views (show also takes a bug id; --json board is JSON)
ddflow wait [--item|--phase]    sleep until something is ready  (--globs: judge the claim you will make)
ddflow wait --approval <id>    block until the operator answers approval <id>, then re-run its gated command
ddflow render                   regenerate docs/ddflow/*.md

ddflow help [topic]             what this is, what it can do, the workflow
ddflow workflow                 the rules this project runs by  (1 = incoherent)
ddflow workflow pipeline ...    set the gates a task or phase passes
ddflow workflow gate ...        define or change one gate
ddflow workflow drop <id>       take a gate out of the pipelines
ddflow import [--apply]         propose an existing project's work  (2 = nothing)
ddflow import --verify          is the import still true, and did anyone finish it?
ddflow history [--item|--kind|--agent|--tail]  one timeline of everything that happened (2 = nothing)

ddflow lesson add|search        capture and retrieve lessons
ddflow research --verdict ..    record a finding (probe required for CONFIRMED/REFUTED)
ddflow bug found|fixed          found files the fix task fix-<id> (--no-task, --globs); fixed needs --regression-test (repeats)
ddflow bug file-tasks [--dry-run]  a fix task for every open bug that has none (one-shot; 2 = none)
ddflow bug reopen <id> --reason R  reopen a bug closed by mistake (fixed or invalid)
ddflow complete <fix task> --regression-test T  closes the bug(s) the task fixes and completes
ddflow task|phase|bug|research list   one line per record, filters, --json (2 = none match)

ddflow session start|prompt|note|end     provenance logging (prompt/note: id optional)
ddflow session adopt-orphans             attach id-less prompts/notes to a session
ddflow session list|show <id>            read sessions back (newest first; --state --owner --since --limit)
ddflow search "<text>" [--exact|--regex]  search every record, session, prompt and the log (2 = no match)
ddflow replay [--out DIR] [--verify]     reconstruct from the log

ddflow recover [--apply]        find crashed agents' work   (2 = nothing)
ddflow cleanup [--apply]        classify ddflow worktrees/branches; --apply lands the safe ones
ddflow pins <file>              which text of an instruction file a test pins, before you compress it
ddflow precommit [--write]      propose a .pre-commit-config.yaml for this repo's stacks (writes only with --write)
ddflow doctor                   integrity + health
ddflow rebuild                  re-derive the index
ddflow export                   list the document kinds with target and state
ddflow export <doc> [filters]   print one (--since --phase --status --limit --max-bytes ...)
ddflow export <doc> --diff|--check|--update|--out P   compare or write (--force, --yes)
ddflow export --all --check     act on the selected documents ([export].documents)
ddflow cadence [--ran NAME]     which periodic passes are due  (2 = none)
ddflow config --explain         every knob, its value, its source and its docs
ddflow config --append-toml ..  add config without a shell editor (validated first; --local: not committed)
ddflow reviewers detect|list|test   find and check cross-family review endpoints
ddflow review <id> --gate ..    run the configured reviewer, record the evidence
ddflow review <id> --gate G --chunk N   re-review only chunk N of the recorded review
ddflow review triage <id> --gate G --finding N --refuted|--confirmed --probe ..
                                record what became of one finding
ddflow reviewers approve [name] a PERSON approves a tool-written reviewer  (no MCP tool)
ddflow mcp                      run the MCP stdio server
```

Every one of these is reachable over MCP, and a test enforces it. One tool goes the
other way and has **no CLI equivalent**, because it has nothing to mean there:

```
ddflow_identify(agent=...)      declare who you are ON THIS CONNECTION (MCP only)
```

A CLI invocation is one process that exits, so it says who it is with `--agent` and the
question does not outlive the command. An MCP connection is a session, so identity is
declared once and persists — see
[Who is calling?](../../parallelism.md#who-is-calling).

---
