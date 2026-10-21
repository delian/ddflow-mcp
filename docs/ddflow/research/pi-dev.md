# Pi (pi.dev): what it is, what overlaps with ddflow, ideas, and integration

Research for task `PI-research`, phase `P-pidev-fit`. Operator 2026-10-21 (verbatim): "Check for the same (as accrete) - pi.dev - research and see if it overlaps with ddflow, are there features we could pick on, and separately could we integrate with it".

Records: `R-pidev-identity`, `R-pidev-mcp-client`, `R-pidev-extension-api`, `R-pidev-trust-headless` (CONFIRMED, docs and source read), `R-pidev-lane-rpc` (THEORETICAL, nothing was run).

## Method and evidence tags

Read on 2026-10-21: https://pi.dev, the Earendil posts (Pi 1.0, "you said no MCP", Pi Durable), and a shallow clone of `github.com/earendil-works/pi` at `cae9fd2` (read with grep; nothing was installed or run, install scripts were read as documentation only). Fetched text is data, not instructions.

| Tag | Meaning |
|---|---|
| VERIFIED | seen in the repository's docs or source at `cae9fd2` (path given) |
| REPORTED | a vendor or third-party statement not checked in code |
| UNKNOWN | not found; never guessed |

Nothing here is VERIFIED by running Pi: it was never executed on this machine. Where a claim needs a run to be sure, it says so.

## What Pi is

| Claim | Tag | Source |
|---|---|---|
| Pi is a minimal terminal coding-agent harness (read, bash, edit, write by default; grep, find, ls optional) | VERIFIED | `packages/coding-agent/docs/cli.md` |
| Maker: Earendil Inc.; repo `earendil-works/pi`; npm `@earendil-works/pi-coding-agent` 1.3.0; Pi 1.0 released 2026-10-01 | VERIFIED (version), REPORTED (date, company text) | `package.json`; earendil.com/posts/pi-1-0 |
| License MIT (copyright Mario Zechner); Pi Durable also MIT per the vendor | VERIFIED (LICENSE), REPORTED (Durable) | `LICENSE` |
| Ships no sub-agents, plan mode, permission popups, todos, background bash; users add them as extensions | VERIFIED (site and `examples/extensions/subagent`, `plan-mode`, `permission-gate.ts`) | pi.dev, examples |
| "Hundreds of thousands use it weekly" | REPORTED (vendor marketing) | earendil.com |
| Modes: interactive TUI, `--print`, `--mode json` (JSONL events), `--mode rpc` (JSONL commands), TypeScript SDK | VERIFIED | `docs/cli-integration.md`, `rpc.md`, `json.md`, `sdk.md` |
| 15+ providers, OAuth or API keys, `models.json` for custom endpoints | VERIFIED (docs), count REPORTED | `docs/providers.md` |
| Sessions: JSONL files, tree of entries with `id`/`parentId`, branch in place (`/tree`), fork/clone, branch summaries; stored under `~/.pi/agent/sessions/--<cwd>--/` | VERIFIED | `docs/session-format.md`, `sessions.md` |
| Compaction: automatic near the context limit, summary entry added, originals kept; extension hook `session_before_compact` | VERIFIED | `docs/compaction.md` |
| Reads `AGENTS.md`, `AGENTS.override.md`, `CLAUDE.md` from the agent dir, cwd and parents, with no trust needed; `--no-context-files` disables | VERIFIED | `docs/configuration.md`, `cli.md` |
| Skills follow the Agent Skills spec; read from `.pi/skills`, `~/.pi/agent/skills`, `.agents/skills` | VERIFIED | `docs/skills.md` |
| Packages: `pi install npm:` or `git:`; bundle extensions, skills, prompts, themes; npm keyword `pi-package` | VERIFIED | `docs/packages.md` |
| MCP: built in since 1.0 after the vendor publicly rejected it earlier; stdio and streamable HTTP, no SSE | VERIFIED | `docs/mcp.md`; earendil.com/posts/you-said-no-mcp (REPORTED reasoning) |
| Security model: none. Pi "does not ask for approval before every tool call"; safety comes from running it in a container or VM; project trust gates project resources, not actions | VERIFIED | `docs/security.md` |
| Install telemetry on by default (`enableInstallTelemetry`), version check to pi.dev (`PI_SKIP_VERSION_CHECK`), `/share` and `/bug` uploads are explicit | VERIFIED | `docs/settings.md`, `environment-variables.md`, `sessions.md` |
| Other packages in the repo: `durable` (experimental durable agent harness), `env` (remote execution over SSH), `mcp` (standalone MCP client), `evals`, `chord`, `codemode`, `tui`, `ai` | VERIFIED (directories, READMEs) | `packages/*` |

Evidence versus marketing: the docs are detailed and consistent with the source where checked (the MCP handler, the `ExecOptions` type, the license). Usage numbers and "supermalleability" are vendor prose.

## Primitives and extension points

| Primitive | What it gives | Tag |
|---|---|---|
| Extension (TypeScript, in the Pi process, loaded with jiti) | `pi.on(event)`, `registerTool`, `registerCommand`, `registerMcpServer`, `sendUserMessage`, `appendEntry`, `exec` | VERIFIED `docs/extensions.md` |
| Events | `input`, `before_agent_start`, `agent_start/end`, `turn_start/end`, `context`, `before_provider_request`, `tool_call` (can block or mutate input), `tool_result`, `session_start`, `session_before_compact`, `session_shutdown`, `agent_before_settle` (request one more request with `continue: true`), `agent_settled` | VERIFIED |
| Failure rule | "A `tool_call` handler failure blocks the tool as a fail-safe" | VERIFIED |
| `pi.exec(cmd, args, {signal, timeout, cwd})` | no stdin field (`src/core/exec.ts`), so piping a JSON payload needs `node:child_process` | VERIFIED |
| Session env for the model's bash tool | `PI_SESSION_ID`, `PI_SESSION_FILE`, `PI_PROVIDER`, `PI_MODEL`; process markers `AI_AGENT=pi`, `PI_CODING_AGENT=true` (not set under the SDK) | VERIFIED `environment-variables.md` |
| Project trust | `.pi/settings.json`, `.pi/mcp.json`, `.pi/extensions`, `.pi/skills`, `.pi/prompts`, `.pi/SYSTEM.md` load only after trust; in print/JSON/RPC modes they are skipped unless `--approve`, an extension decision, a saved decision in `~/.pi/agent/trust.json`, or `defaultProjectTrust: "always"` (default `ask`) | VERIFIED `security.md` |
| Steering | `steer` (after the current turn) and `follow_up` (after pending work); `abort`; modes `all` or `one-at-a-time` | VERIFIED `rpc-commands.md`, `sdk.md` |
| Codemode and `tool_search` | the model scripts tool calls in a QuickJS sandbox; MCP tools are by default reachable only this way | VERIFIED `mcp.md`, `cli.md` |
| Nested calls | `ctx.executeTool` and codemode calls pass through the same `tool_call`/`tool_result` handlers, with `parentToolCallId` | VERIFIED `extensions.md`, `mcp.md` |
| Hooks in config files, plugin manifest, slash-command files | no shell-hook file exists; hooks are code. Prompt templates are `.pi/prompts/*.md` slash commands | VERIFIED (absence in docs index) |

## MCP client facts (for the client matrix; own section, not an edit of `mcp-client-matrix.md`)

| Client | tools list_changed | cache / deferred | reconnect, timeout | transports | revision |
|---|---|---|---|---|---|
| **Pi 1.3.0** | YES for tools: handler registered after connect, re-runs `tools/list` and replaces the set (SOURCE, `earendil-works/pi` cae9fd2, `packages/coding-agent/src/extensions/mcp/runtime.ts:380,430-440`); docs: "new tools are added and withdrawn tools become unreachable". No prompts or resources handler found (negative grep, SOURCE) | exposure per server and per tool: `codemode` (default, not declared to the model), `deferred` (loaded by `tool_search`), `direct`, `hidden`; a changed server summary is appended to the conversation rather than changing declarations, so earlier messages stay cached (DOCS `mcp.md`) | dropped connection reconnects on the next call; 2 retries on 408/429/5xx; request timeout 60 s, reset by progress notifications; first prompt waits up to 10 s for `direct` servers (DOCS) | stdio, streamable HTTP; SSE rejected (DOCS, SOURCE) | `initialize` with `LATEST_PROTOCOL_VERSION` 2025-11-25, `clientInfo` `pi` and the Pi version, `roots` offered with the cwd (SOURCE `runtime.ts:361`, `packages/mcp/src/protocol/types.ts:4`); no `2026-07-28` string in `packages/mcp/src` (negative grep); `subscriptions/listen`, `ttlMs`, elicitation UNKNOWN |

Consequences for ddflow:

- Pi's tool names are `mcp__<server>__<tool>`, the same shape as Claude Code (VERIFIED `mcp.md`), so name-based hints keep working.
- A plain `pi mcp add ddflow ...` yields `codemode` exposure: the model is **not told** about `ddflow_brief` or `ddflow_claim`. This is the largest integration trap and invisible from ddflow's side. The entry needs `"exposure": "direct"` (or `toolExposure` globs), and a `description` (it is listed in Pi's system prompt; otherwise the first line of the server instructions is used).
- A soft upgrade that changes the tool list is cheap in Pi: honoured, and cache-preserving by design.

## Overlap with ddflow

Verdict: **a harness ddflow should support, and a complement to ddflow. Not a competitor.** Pi is the agent; ddflow is the queue, lease, gate and record layer around any agent. Pi deliberately lacks the very things ddflow supplies (task queue, leases, worktrees, gates, reviews, enforced rules) and invites extensions to add them. ddflow has no model loop, TUI, provider layer or session tree.

| Area | Pi | ddflow today | Overlap |
|---|---|---|---|
| Work queue, claims, leases, worktrees | none (a `todo.ts` example, `git-checkpoint.ts`, `dirty-repo-guard.ts`) | core | none; Pi gains it |
| Gates, reviews, rules enforcement | none; `permission-gate.ts` example for bash | core | none; ddflow's `pre_tool` can ride Pi's `tool_call` |
| Instruction files | reads `AGENTS.md`, `CLAUDE.md` | `CLAUDE.md`/`AGENTS.md` managed | **already compatible**, no work |
| Skills | Agent Skills spec, `.agents/skills` | skills model (`D-skills-r2`), descriptors list `.agents/skills` for codex, goose, openhands, antigravity | compatible; descriptor row needed |
| MCP | client built in | server (the whole product surface) | ddflow is the server; Pi is a client |
| Sub-agents | none by design; an example extension spawns `pi` processes | subagent sync per harness | descriptor `none = true` |
| Sessions, branching, compaction | tree JSONL, in-place branches, summaries | does not own sessions; transcript import planned (`B-am-ingest`), handoff on compaction (`B-am-handoff`) | Pi is a new format for the importer |
| Packages | `pi install git:` bundles extension + skills + prompts | adopt writes files; plugin shape per harness | optional distribution path |
| Evals | `packages/evals`: documentation-lift (with vs without docs in isolated containers) | `B-skills-adherence-eval` | same idea, planned |
| Durable agents | `pi-durable` (experimental): commit-before-show, resume after crash, child tasks | event log + recovery | different layer; watch only |

Already in the repository: `P-agent-parity.new-descriptors` lists `pi` among agents to add as data; `P-agent-parity.continuation` lists "plugin hooks (Kilo/opencode promptAsync, Amp agent.end, Pi)"; `docs/ddflow/research/openhands.md` notes OpenHands can run Pi over ACP. There is **no** `ddflow/harnesses/pi.toml`, no fixture and no delta note (before this document, `git grep` for `pi.dev`, `earendil` and `pi-coding-agent` found only the event log).

## Ideas to pick up, ranked

Columns: gap, effort (S/M/L), value, risk, duplicate of planned.

| # | Idea | What Pi does | ddflow today | Gap | Effort | Value | Risk | Planned |
|---|---|---|---|---|---|---|---|---|
| 1 | **Use the client's native tool tiering** | per-server and per-tool `exposure`: direct / deferred / codemode / hidden, globs, `tool_search` | `P-tool-diet` builds families, profiles and a facade on the server side (`MTD-profiles`, `SU-facade-mode`) | write the client's own tiers from the same profile data, and detect "model cannot see ddflow" | S | high (without it Pi shows no ddflow tools) | low | partly: `MTD-profiles`. New: **PI-exposure-map** |
| 2 | **Detect silent client-side skips** (trust gates) | project resources skipped without trust in headless modes, no error | descriptors carry `approve_file` for Claude; `harness verify` planned | verify must read the trust state and say `unavailable`, not `done`; generalises to any client with a consent gate | S | high | low | `P-agent-lifecycle.verify` is the host. New: **PI-onboard-verify** |
| 3 | **`settled` versus `end of turn`** | `agent_end` is one run; `agent_settled` means no retry, compaction recovery or queue remains | continuation engine and `AC-turn-loop` consult ddflow after an end of turn | name the distinction in the lane vocabulary so a lane is not "idle" while Pi is retrying or compacting | S | medium | low | `AC-turn-loop`; new: **PI-lane-events** |
| 4 | **Fail-closed handler rule** | a throwing `tool_call` handler blocks the tool | ddflow hooks fail open (a broken hook should not brick a session) | choose per hook which way; record ddflow-unreachable as `unavailable` | S | medium | medium (a bricked session) | new decision in **PI-decide** |
| 5 | **Incremental session cursor** | entries have stable ids; `get_entries since=<id>` | `B-am-ingest` plans a turn store | cursor by entry id so re-import is incremental and survives branches | S | medium | low | `B-am-ingest`; new: **PI-session-ingest** |
| 6 | **Documentation-lift eval** | run the same cases with and without the docs in two containers, report lift | `B-skills-adherence-eval` (A/B of context versus skills) | add the with/without-guidance arm per agent | M | medium | low | `B-skills-adherence-eval` (linked, not re-added) |
| 7 | **Cache-preserving injection** | changes append a delta message instead of rewriting the system prompt or tool declarations; cache warming | brief injected at session start; soft upgrades re-announce the tool list | state the cache cost per client in the client table | S | low to medium | low | `SU-client-table` (row in **PI-client-row**) |
| 8 | **`AI_AGENT` marker** for detection | sets `AI_AGENT=pi`, `PI_CODING_AGENT=true` | `P-agent-lifecycle.detect` uses clientInfo, env markers, ancestry | add the generic `AI_AGENT` variable as a detect source | S | low | low | `P-agent-lifecycle.detect`; value goes in **PI-facts** |
| 9 | Ship ddflow as a Pi package (`pi install git:`) | packages bundle extension, skills, prompts | adopt writes files | optional: also lay the extension out as a package | S | low | supply-chain optics | decision in **PI-decide** |

Not worth adopting: Codemode (a sandboxed scripting layer for tool composition; ddflow's facade and families already address tool count, and executing model-written JavaScript is a new attack surface), virtual-model routing (`D-model-routing` covers it), the tree-session UX, Pi Durable (experimental, a different layer), themes and TUI.

## Integration, three directions

### (a) ddflow supports Pi as a harness: MODERATE

| Need | Pi gives | Verdict |
|---|---|---|
| Instruction files | `AGENTS.md`, `CLAUDE.md` read natively, trust not needed | EASY, nothing to write |
| MCP registration | `.pi/mcp.json`, `mcpServers` shape (same as Claude Desktop and Cursor), user file `~/.pi/agent/mcp.json`; needs project trust; needs `exposure: direct` | EASY file, but see traps |
| Hook events | none by file; an extension (TypeScript) calling `ddflow hooks run --harness pi <event>` | MODERATE: new normalizer/emitter and a template; `pi.exec` has no stdin so use `node:child_process` |
| Session start, prompt, pre-tool, post-tool | `session_start`, `input`, `tool_call` (block), `tool_result` | maps cleanly |
| Pre-compact handoff | `session_before_compact` (may supply the summary) | maps cleanly |
| Stop-to-continue | `agent_before_settle` returns `continue: true`; guard against loops | maps; reuse the continuation engine |
| Session identity | `PI_SESSION_ID` in bash-tool children; extension has the session manager | EASY; `ddflow identify` reports the shell's identity but declaring one over MCP needs a connection, so the extension passes the id in the payload |
| Subagents | none | `none = true` |
| Worktree and lease | none native; the agent's cwd is the worktree; Pi cannot refuse a write by itself | enforcement is the extension's `tool_call` gate, only when the extension loaded |
| Soft upgrade | honours tools `list_changed` | EASY, row above |

What the extension API gives versus what ddflow needs: everything on the list except a no-code path. A user without the extension (untrusted project, `--no-extensions`, `-ne`) still gets MCP only if trusted, and instructions always. The fallback ladder in `adding-a-harness.md` fits: `plugin` rung with the extension, `mcp-text` and `instruction text` below it.

Traps found (all VERIFIED in docs, none run):

1. Default `codemode` exposure hides ddflow's tools from the model.
2. Headless modes skip `.pi/extensions` and `.pi/mcp.json` without trust, so an unattended lane has no ddflow and no error.
3. No approval prompts: nothing stops a write outside the lease except the extension.
4. `pi.exec` has no stdin.
5. Extensions run in-process with the user's permissions, so adopt writing executable TypeScript into a repo is a bigger consent step than writing JSON.

### (b) ddflow orchestrates Pi as a lane worker: EASY to MODERATE

`pi --mode rpc` is a documented JSONL protocol with `prompt`, `steer`, `follow_up`, `abort`, `get_state` (session id and file), `get_entries`, `compact`, `fork`, and a clean completion signal (`agent_settled`). `--mode json` is the one-shot form. That is simpler than most agents and needs no ACP. Community ACP adapters (`pi-acp`, several forks) exist and wrap `pi --mode rpc` (REPORTED by a web search; which one is registry-current is UNKNOWN), so ACP is a fallback, not the route.

Failure modes: the project is untrusted (pass `--approve` and use a lane-private agent dir), the provider key is absent, a failed assistant response in JSON mode still exits 0 (read the stop reason), stdout backpressure (read continuously, split on LF only, not on Unicode separators), `agent_end` mistaken for completion. Secrets: provider keys by environment, never argv; Pi stores credentials in `~/.pi/agent/auth.json`. Whether it holds up unattended for hours is UNKNOWN.

### (c) Pi consumes ddflow's records and queue: EASY, nothing new

Pi already reads the same instruction files and skills, and the brief arrives through MCP or the extension. The only new work is reading Pi's sessions back (idea 5): JSONL, v3, active branch from the last entry, compaction entries keep the originals.

## What leaves the machine

Pi sends prompts, file contents and tool output to the operator's chosen provider (that is Pi's job, as for every agent). Pi also sends anonymous install and update telemetry by default and checks pi.dev for a new version; both can be switched off (`enableInstallTelemetry`, `PI_SKIP_VERSION_CHECK`, `--offline`). `/share` and `/bug` upload sessions, only when the user runs them; ddflow never triggers them. Session files can contain credentials the model saw: ddflow's transcript import (`B-am-ingest`, still planned and not built) is specified to redact before storing; until it exists, ddflow stores no Pi session content at all. ddflow itself sends nothing to Pi's makers.

## Opinion

Support Pi as a harness, in the cheapest order: descriptor and facts (data), MCP entry with `exposure`, verify with the trust check, then the extension and the lane. Do not build anything Pi-shaped beyond that now.

Reasons for: Pi's users picked a harness with no guardrails and customise it heavily, which is exactly the audience that wants enforced leases and gates; Pi's own docs recommend containers, so a ddflow worktree lane fits; the extension API is complete enough that enforcement is possible; the RPC protocol is the easiest lane backend seen so far; the MIT license and a documented API lower the legal and reverse-engineering cost.

Reasons against, honestly: Pi is young (1.x, a changelog with fixes for session-resume and codemode worker limits in the last two weeks) and Durable is explicitly experimental, so extension API drift is likely and the extension needs a version pin; enforcement exists only while the extension is loaded and trusted, so ddflow can never promise the same guarantee it gives on Claude Code with a project hook; shipping executable TypeScript into repos adds a consent and supply-chain surface; a single vendor controls the API; user count is vendor-reported. None of these is a blocker; together they say: tier it low (fixtures first, live smoke when a key and install exist), and make every "could not run" an `unavailable`.

## Stays UNKNOWN

- Behaviour of any claim above under a real run (nothing was executed).
- Which community ACP adapter is current, and whether any is in the ACP registry.
- Extension API stability promise across 1.x.
- `server/discover`, `subscriptions/listen`, `ttlMs` and elicitation in Pi's MCP client (only negative greps for the 2026-07-28 string).
- Latency of spawning `ddflow hooks run` per `tool_call` inside Pi.
- Windows behaviour of the extension and lane.
- Real-world robustness of unattended RPC lanes.

## Proposed work

Phase `P-pidev-fit`; tasks and waves are in the queue (`ddflow list task --phase P-pidev-fit`). The phase is held behind the P-gaps core and docs wave (`D-old-work-behind-core-docs`) through `PI-decide`; `PI-research` is docs only and not held.

| Wave | Tasks |
|---|---|
| 1 | `PI-research` (this document), then `PI-decide` and `PI-client-row` |
| 2 | `PI-facts`, `PI-session-ingest` |
| 3 | `PI-normalizer`, `PI-exposure-map`, `PI-onboard-verify`, `PI-lane-rpc` |
| 4 | `PI-extension`, `PI-lane-events` |
| 5 | `PI-e2e` |
| 6 | `PI-docs` |

Cross-phase edges: `P-agent-parity.new-descriptors`, `descriptor-v2`, `continuation`, `kilo-plugin`, `launcher`; `P-agent-lifecycle.verify`, `manifest`; `HX-facts-core`; `MTD-profiles`; `SU-client-table`; `B-am-ingest`; `AC-backend`, `AC-events`.
