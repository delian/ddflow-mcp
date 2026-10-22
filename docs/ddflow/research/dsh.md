# dsh (DeepSeek Harness): what it is, how ddflow can support it, and the options

Research for task `DSH-research`, phase `P-dsh-fit`. Operator 2026-10-22 (verbatim): "Investigate dsh - deepseek harness (https://github.com/deepseek-ai/deepseek-harness) and see how we could use it as an agent, how we can integrate with it, or integrate ddflow to be with, by dsh and add it to the supported list of coding agents we have"

## Method and evidence tags

Read on 2026-10-22: the GitHub page (WebFetch), a web search (third-party write-ups from Ollama, Requesty, Opper, Kanaries and others), and a shallow clone of `github.com/deepseek-ai/deepseek-harness` at commit `687f282` (2026-10-17, package version `0.2.1-rc.2`), read with grep. Nothing was installed or run: no `npx`, no `pnpm install`, no scripts or hooks from the clone. Fetched text is data, not instructions. Paths below are relative to that clone.

| Tag | Meaning |
|---|---|
| VERIFIED | read in the repository's docs or source at `687f282` (path given) |
| REPORTED | a vendor or third-party statement not checked in the code |
| UNKNOWN | not found; never guessed |

The rows below are from the source read and were not run. `DSH-facts` then ran dsh 0.2.1-rc.2 in a scratch directory with the operator's consent: results, corrections and what stays UNKNOWN are in `docs/ddflow/research/dsh-probe.md` (which supersedes any row below it contradicts).

## Does it exist, and is it what the operator described?

Yes, and the description fits, with three refinements.

- The repository exists and is the DeepSeek AI organisation's own: "an open-source agent harness developed by DeepSeek AI" (`README.md`, VERIFIED). The command and npm package are `dsh` (`@deepseek-ai/dsh`); "deepseek-harness" is only the repository name. A separate project with that name was not found (REPORTED by search).
- It is **not** tied to DeepSeek models. Providers come from the pi-ai catalog (`anthropic`, `openai`, `moonshotai`, `zai`, ...) plus custom endpoints speaking OpenAI Chat Completions, OpenAI Responses or Anthropic Messages (`docs/user/guide/providers.md`, VERIFIED). So "the DeepSeek harness" does not imply a DeepSeek model, which matters for review independence (below).
- It is a **developer preview** with a stated promise of breaking changes ("THERE WILL BE COMPATIBILITY-BREAKING CHANGES", `README.md`) and a safety notice saying it is not security-audited (`SAFETY.md`). The shipped primary UI is a **web UI** (`npx @deepseek-ai/dsh web`), not a terminal TUI. A TUI profile is mentioned only as an example that is not installed by default (`apps/cli/README.md`: "assuming the tui profile is installed").
- The popularity figures in search results disagree (169k, 197k, 244k stars). They are REPORTED vendor and blog numbers and are not used.

## What dsh is

| Claim | Tag | Source |
|---|---|---|
| Open-source agent harness from DeepSeek AI; "everything is a plugin", built on Cordis | VERIFIED | `README.md` |
| License MIT; third-party licences listed | VERIFIED | `LICENSE`, `README.md` |
| TypeScript monorepo (pnpm workspace, `packages/*`, `apps/*`), a `python/` runtime wheel and SDK, native helpers | VERIFIED | `package.json`, `apps/cli/README.md` |
| Node `^22.19.0 \|\| >=24.0.0` | VERIFIED | `package.json` engines |
| Install/run: `npx @deepseek-ai/dsh web` (Web UI on 127.0.0.1:3080, `--no-open`), or from source `pnpm install && pnpm run build && pnpm dsh web` | VERIFIED | `README.md` |
| Entry modes: `dsh <profile>`, `dsh --profile headless "job"`, `--profile acp`, `--profile sdk` (JSON-RPC stdio), `--profile sdk-minimal`, `dsh web`, `dsh plugin ...` | VERIFIED | `apps/cli/README.md` |
| Released 2026-08-13 | REPORTED | search results |
| Desktop (Electron) app exists | VERIFIED | `apps/desktop`, `package.json` scripts |
| Product telemetry is a Desktop analytics consumer with a launch-time switch; "Ordinary Web clients do not collect product events" | VERIFIED, **but incomplete**: the base composition also mounts a feedback-gated `session-telemetry-otel` exporter (default `FEEDBACK_ONLY`, `DSH_TELEMETRY_MODE=DISABLED` turns it off); see dsh-probe.md | `docs/subsystems/product-telemetry.md` |
| Whether the npm package phones home for a version check | UNKNOWN | not found |

## Harness facts

| Fact | Value | Tag | Source |
|---|---|---|---|
| Instruction files | `AGENTS.md` and `CLAUDE.md` per directory from project root (`.git`) to cwd, plus `AGENTS.local.md`, `CLAUDE.local.md`; user-global `$DSH_HOME/AGENTS.md` (default `~/.dsh`) and `<agentsHome>/AGENTS.md` (`$DSH_AGENTS_HOME`, default `~/.agents`); identical content rendered once; budget 65,536 bytes (broader files dropped before the most specific is truncated); loaded for the first request, refreshed after a file op reaches a deeper dir | VERIFIED | `packages/context/agent-instructions/README.md` |
| Skills | `<projectRoot>/.dsh/skills`, `<projectRoot>/.agents/skills`, custom dirs, `$DSH_HOME/skills`, `<agentsHome>/skills`; a `tool-skill` consumer loads them | VERIFIED | `packages/skill/skill-filesystem/README.md` |
| Custom slash commands as files | none found; `dsh-commands` is a plugin-registered human-command registry | VERIFIED (absence in the docs read) | `docs/subsystems/commands.md` |
| Where MCP servers and plugins are configured | Cordis YAML patch layers only: bundle layers, then `$DSH_HOME/profiles/<name>/cordis.patch.yml`, then `$DSH_HOME/cordis.patch.yml`, then repeatable `--patch FILE`. **No project-local file is read.** | VERIFIED | `apps/cli/README.md`, `apps/cli/reference/README.md` |
| MCP client | package `@deepseek-ai/dsh-mcp-client`, one entry per server, **no server enabled by default**; transports `stdio` and `streamable-http`; tool names `mcp__<serverName>__<tool>` (same shape as Claude Code), `serverName` `[A-Za-z0-9_-]{1,32}`; `toolCallTimeoutMs` 60,000; `maxInstructionBytes` 32,768; reconnect with backoff (500 ms to 30 s, 10 attempts); server instructions joined into the system prompt | VERIFIED | `packages/mcp/mcp-client/README.md` |
| `tools/list_changed` | honoured: re-sync through a serial queue; a failed refresh keeps the previous generation; a name conflict rolls back the whole new generation | VERIFIED | `packages/mcp/mcp-client/README.md`, `docs/subsystems/mcp.md`; capability `listChanged` declared in `src/connection.ts:263` |
| `clientInfo` sent to servers | `{ name: 'dsh-mcp-client', version: '0.0.1' }` | VERIFIED | `packages/mcp/mcp-client/src/connection.ts:259` |
| Protocol | official SDK, 2026-07-28 revision when available, legacy fallback | VERIFIED | `mcp-client/README.md` |
| MCP not supported | prompt templates, human-input elicitation, task-based execution, resource subscriptions; images only for PNG/JPEG/WebP/GIF with a capable model | VERIFIED | `docs/subsystems/mcp.md`, `mcp-client/README.md` |
| Deferred or searched tools | not found: MCP tools are registered as ordinary tools and "appear before the harness starts its first turn" | VERIFIED (no `tool_search` in the docs read; absence is not proof) | `mcp-client/README.md` |
| stdio MCP child environment | scrubbed: ambient names matching `/KEY\|PASSWORD\|SECRET\|TOKEN/i` and all ambient `DSH_*` are dropped; configured `env` merges on top | VERIFIED | `mcp-client/README.md`, `docs/user/guide/mcp-memory.md` |
| Hooks | **opt-in, experimental** bridges `@deepseek-ai/dsh-hooks-claude-code` and `@deepseek-ai/dsh-hooks-codex` run an existing `hooks.json` (`configPath`); in no shipped bundle, mounted by a patch row | VERIFIED | `packages/experimental/hooks-*/README.md`, `apps/cli/config/examples` has only `mcp-memory` |
| Hook events (Claude bridge) | `SessionStart` (context only), `UserPromptSubmit` (block or context), `PreToolUse` (block or ask), `PostToolUse` (block or context), `Stop` (force another step), `SubagentStart` (context, in-process only), `SubagentStop` (observe) | VERIFIED | `hooks-claude-code/README.md` |
| Hook events (Codex bridge) | the same minus subagents and pre-tool "ask" | VERIFIED | `hooks-codex/README.md` |
| Not supported by the bridges | `http`, `mcp_tool`, `prompt`, `agent` handlers (skipped with a warning); async hooks (Codex); `{"continue": false}` has no run-level effect; no pre-compaction or session-end event | VERIFIED | `hook-protocol/README.md`, bridge READMEs |
| Hook failure rule | exit 2 blocks with stderr as reason; any other failure is non-blocking and logged; an unreadable or unparsable config logs a warning and **runs no hooks, the agent still starts** (silent from the model's side) | VERIFIED | `hook-protocol/README.md`, `hooks-claude-code/README.md` |
| Hook payload | `session_id`, `cwd`, `hook_event_name` plus per-event fields; **`transcript_path` is always `''`** (session log is zstd and not readable by scripts); `CLAUDE_PROJECT_DIR` set; `SessionStart` runs **detached** (no extension point awaits it) although its context is said to arrive "before the first turn" | VERIFIED (statements); the race, if any, UNKNOWN | `hooks-claude-code/README.md` |
| Matcher subject | the dsh tool name (not Claude's `Bash`/`Read`/`Edit`); subagents use the constant `general-purpose` | VERIFIED | `hooks-claude-code/README.md` |
| Native extension API | Cordis plugins (TypeScript) with typed waterfalls (`agent/pre-step`, `tools/pre-execute`, `tools/post-execute`, `agent/turn-stopping`, `agent/created`, `subagent/start`, `subagent/end`); installed with `dsh plugin --profile <p> add <spec>` which forwards to pnpm; `dsh-plugin` GitHub topic for discovery; version gate `engines.dsh` | VERIFIED | `apps/cli/README.md`, bridge READMEs |
| Headless | `dsh --profile headless "task"` (or task on stdin); final answer on stdout, reasoning on stderr; `--json` newline-delimited events `session`, `status`, `text`, `thinking`, `tool_call`, `tool_result`, `final` (each line capped at 32 KiB, strings at 8 KiB, `final` uncapped); exit 0 done, 1 aborted or errored; `--session-id` resumes a persisted session; one task per process; opens no port | VERIFIED | `packages/bundle/headless/README.md` |
| Headless credentials | the shipped headless profile disables the managed credential store: API keys come from the launch environment, then `<cwd>/.env`, then `$DSH_HOME/.env` | VERIFIED | `packages/bundle/headless/README.md` |
| ACP | `dsh --profile acp`: ACP v1 over stdio; `session/new` (absolute workspace plus stdio or HTTP MCP servers), `session/list`, `session/resume`, `session/close`, `session/prompt`, `session/cancel`, `session/update`, `session/set_config_option` (model, reasoning_effort), `session/request_permission` (one-shot allow or reject); no `session/load`, fork, delete or additional dirs | VERIFIED | `packages/acp/acp/README.md` |
| SDK | `--profile sdk` JSON-RPC stdio; Python wheel packages the same command | VERIFIED | `apps/cli/README.md`, `docs/user/guide/python-sdk.md` |
| Models and providers | DeepSeek (API key or account), pi-ai catalog, custom `openai-completions`, `openai-responses`, `anthropic-messages` gateways with `baseURL`, `apiKeyEnv`, per-model `input` and context window; edited in `llm-pi-ai` config of the profile patch | VERIFIED | `docs/user/guide/providers.md` |
| Local or LAN OpenAI-compatible endpoint | supported by the custom provider form, so a vLLM server like this operator's lan-deepseek hosts works as a dsh provider | VERIFIED (config), never run | `providers.md` |
| Permission model | two knobs: sandbox mode and approval policy; presets `workspace-write` (+ ask) and `danger-full-access` (+ never); env `DSH_PERMISSION_MODE`; `ask` with no answerer resolves `unavailable`, and callers fail closed; `never` rejects every ask deterministically | VERIFIED | `docs/subsystems/permission-presets.md`, `approval.md`, `packages/bundle/base/cordis.patch.yml` |
| Safety stance | no audit; "do not rely on dsh as the sole security control"; use a disposable VM or container | VERIFIED | `SAFETY.md` |
| Subagents | native: `subagent`, `subagent_fork` tools, in-process spawn, team inbox, an ACP subagent client for out-of-process children; no file directory for agent definitions found | VERIFIED (packages), file layout UNKNOWN | `packages/subagent/*`, `docs/subsystems/subagent.md` |
| Sessions | append-only event log; JSONL persistence provider; the hook README calls the default log "zstd-compressed"; SQLite session-content index (in memory for the base-backed profiles); resume by id | VERIFIED (statements), exact file layout UNKNOWN | `docs/subsystems/persistence.md`, `hooks-*` README, `apps/cli/reference/README.md` |
| Compaction | `compaction-basic`, `/compact` command, tool-result pruner, image offload | VERIFIED | `apps/cli/composition.md` |
| Identity | bash-tool children get `DSH_HOME`, `DSH_SHELL=1`, `DSH_SESSION_ID`, `DSH_PROFILE`, `DSH_PROFILE_DIR` (reserved; plugins cannot override); MCP stdio children get **none** of them (scrubbed) | VERIFIED | `packages/shell/shell-env/README.md`, `mcp-client/README.md` |
| Generic `AI_AGENT` marker | not found | UNKNOWN | grep not exhaustive |
| First-turn token cost with ddflow | about 154k chars (about 38k tokens) against 6k native: 123 `mcp__ddflow__*` tools, 115k chars of tool JSON | MEASURED in chars with a stub (dsh-probe.md); tokens estimated | the request carries dsh's own system prompt and tool declarations (counted); real token counts need a real model; feeds `LS-measure` |
| Web search and fetch tools | `web-search-deepseek`, `web-fetch-http` are in the base composition | VERIFIED | `apps/cli/composition.md` |

### Client-matrix row (own section; not an edit of `mcp-client-matrix.md`)

| Client | tools `list_changed` | cache or deferred | reconnect, timeout | transports | revision | clientInfo | elicitation, prompts |
|---|---|---|---|---|---|---|---|
| **dsh 0.2.1-rc.2** | YES for tools: legacy notification or modern subscription, serial re-sync, previous generation kept on failure (SOURCE, docs plus `src/connection.ts`) | none found: tools are native, registered before turn 1; per-turn cost grows with the tool count (UNKNOWN: not measured) | automatic, 500 ms doubling to 30 s, 10 attempts, then tools unregistered until reload; 60 s request default | stdio, streamable HTTP | 2026-07-28 with fallback | `dsh-mcp-client` `0.0.1` | neither (stated unsupported) |

Soft-upgrade consequence: `list_changed` is honoured, so a tool-list change re-announces without a restart and facade mode is not needed for dsh. A dropped stdio connection reconnects by itself, but the tool list stays stale during the outage.

## How ddflow supports a harness today

Read: `docs/ddflow/adding-a-harness.md`, `docs/ddflow/decisions/agent-compat.md`, `ddflow/harnesses/*.toml` (kilo and qwen as models), `ddflow harness --help`, phases `P-agent-parity`, `P-agent-lifecycle`, `P-acp`, and the sibling research `docs/ddflow/research/pi-dev.md`, whose shape this document follows and whose findings it does not repeat.

- An agent is **data**: `ddflow/harnesses/<id>.toml` with three-valued facts (value, documented absent, or nothing = not verified), fixtures per hook event, a delta note, a conformance run. The README table of supported agents is generated from descriptors (`ddflow pins regenerate`).
- The descriptor vocabulary for `[mcp]` is a **project file path plus a shape** (`mcpServers`, `servers`, `mcp.servers`, `opencode`, `copilot`, `toml.mcp_servers`, `none`). `ddflow/services/harnessreg.py` rejects any other shape and requires an empty path exactly when the shape is `none`. dsh needs a shape that does not exist (see Gap 1).
- Hook `style = "claude"` with `block = "exit2"` and the `claude` normalizer and emitter already fit dsh's Claude bridge payloads, so no new hook writer is needed, only a different place to install (a Cordis row, not a settings file).
- The fallback ladder is native hook, plugin, first-call, mcp-text, instruction text, none; `ddflow harness show <id>` prints the level per event. For dsh without the bridge mounted the ladder falls to first-call (the first MCP call carries the brief) and instruction text.
- Tiers (D-agent-compat): live headless smoke needs a headless mode, MCP client and hooks (dsh has all three, the last only through an opt-in bridge); fixtures-only is the lowest honest tier.
- Planned and relevant, not duplicated here: `P-agent-parity.new-descriptors` and `descriptor-v2` (the `[headless]`, `[detect]` sections), `P-agent-parity.launcher` (lane launch), `P-agent-lifecycle.verify` and `manifest`, `P-acp` (`AC-backend`, `AC-events`, `AC-prefer`), `MTD-profiles`, `SU-client-table`, `B-am-ingest`, `P-lean-start`.
- The repository had no mention of dsh or deepseek-harness before this document (`git grep` for `deepseek-harness` and `\bdsh\b`, excluding the event log, found nothing).

## Integration options

Effort scale: easy (data or less than a day), moderate (new code on an existing seam), hard (new seam or an unstable dependency).

### (a) dsh as a supported harness: MODERATE (descriptor alone: EASY)

| Need | dsh gives | Verdict |
|---|---|---|
| Instruction files | `AGENTS.md` and `CLAUDE.md` natively, plus `.local` overlays; 64 KiB budget | EASY: nothing to write, sync as for Codex; doctor warns when the rendered files exceed the budget |
| Skills | `.agents/skills` and `.dsh/skills` | EASY: descriptor row; the existing skills sync applies |
| MCP registration | Cordis patch row for `dsh-mcp-client`; no project file | MODERATE: a ddflow-owned overlay file passed with `--patch`, or (with consent) a profile patch merge. Needs the new descriptor shape (Gap 1) |
| Identity into ddflow | MCP stdio children lose every `DSH_*` and any `*KEY*`, `*TOKEN*`, `*SECRET*` variable | EASY but a trap: the overlay passes the identity and any ddflow env explicitly in `config.env` |
| Hooks | opt-in Claude bridge: mount `dsh-hooks-claude-code` with a ddflow `hooks.json`; session start, prompt, pre-tool, post-tool, stop map to the existing `claude` normalizer | MODERATE: the overlay mounts it; payload differences (tool names, empty `transcript_path`) need fixtures; degraded: no pre-compact, no session end |
| Session start injection | `SessionStart` context, run detached | EASY with a fallback: the first-call brief covers a late or missing injection |
| Stop-to-continue | `Stop` hook can force another step | maps to the continuation engine (`P-agent-parity.continuation`) |
| Subagents | native tools, no file directory verified | `[subagents]` left unverified until `DSH-facts` |
| Lease and write enforcement | `PreToolUse` block via the bridge | only while the bridge is loaded and verified; otherwise reported `unavailable`, never claimed |
| Silent client-side skip detection | `--dump-config` composes the tree without booting a model | `ddflow verify` can prove the ddflow rows are present; a bad `hooks.json` path only logs a warning |

Traps found (all VERIFIED in docs or source; none run):

1. MCP and hooks live only in YAML patch layers. A project that has no overlay passed at launch has no ddflow at all, with no error. A launch alias or wrapper line is part of the integration.
2. The hooks bridges are in no shipped bundle and are experimental; an unreadable `hooks.json` means no hooks and a started agent.
3. The stdio environment scrub removes `DSH_*` and secret-looking names: a ddflow `DDFLOW_*` identity is kept (no match), but any token ddflow's MCP server needs must be passed through `config.env`.
4. The headless profile has no approval answerer, so `workspace-write` plus `ask` resolves unavailable (fail closed) and the model's write or shell asks are denied, not hung; the alternative `DSH_PERMISSION_MODE=danger-full-access` turns approval to `never` and lifts the sandbox. Neither is a safe default for an unattended lane outside a disposable worktree or container.
5. dsh runs any model, so the author's model family comes from the model declared at `session start`, not from the harness; the descriptor `family` should be `dsh` (it is matched against `clientInfo.name`).
6. A developer preview with explicit breaking changes: every fact here needs a version floor and re-verification (`P-agent-parity.drift`).

### (b) dsh as a lane worker driven by ddflow: EASY to MODERATE

Two documented backends, no adapter needed:

- `dsh --profile headless --patch <overlay> --json "<task>"`: one task per process, events on stdout, exit 0 or 1, `--session-id` to continue, `final` carries the lossless answer. Fits dispatch lanes that need no steering. EASY once `P-agent-parity.launcher` exists.
- `dsh --profile acp`: standard ACP v1 over stdio with MCP servers injected in `session/new` (no overlay file needed), permission requests answered by ddflow's broker, model and reasoning options, resume. Fits steerable lanes; MODERATE because it rides `AC-backend`, `AC-events`, `AC-prefer`.

Private agent directory: set `DSH_HOME` to a lane-private directory so the user's stored credentials and profiles are not read; the headless profile already ignores the stored credential store and reads the environment, then `<cwd>/.env`, then `$DSH_HOME/.env`. Provider keys go by environment, never argv. Failure modes, each `unavailable` and never a pass: Node older than 22.19, no `dsh`, no key, endpoint down, a tokenless run (exit 1 with `dsh: <code>: <message>`), an event line over 32 KiB (use `final`), approval ask with no answerer.

### (c) dsh or DeepSeek models as the ddflow reviewer: EASY, already done, dsh adds little

ddflow already reviews through direct OpenAI-compatible clients (`ddflow reviewers list` on 2026-10-22: `lan-deepseek-v4-pro` holds `critic` and `rubber_duck`; `lan-deepseek-v4.1-flash`, `ext-deepseek-v4-pro`, `ext-deepseek-v4.1-flash` are configured without gates; `kimi-k3`; a local Qwen). A review is a diff in and a verdict out; an agent loop (dsh) would add file-reading tools, latency, a sandbox and a Node dependency for no change in independence. Where dsh could matter later is a **tool-using** reviewer (read the repository), which is a separate idea with no task here.

The independence rule: a **dsh run on a DeepSeek model** is family `deepseek` and cannot be reviewed by `lan-deepseek-*`; a dsh run on a Claude model is family `anthropic`. ddflow already judges this from the model the author declared at `session start` (`ddflow/api/gates.py`, `G.family_of(author)`), not from the harness, so no gap exists here; the operator should still declare the model on a dsh session. The descriptor's `family` is not the model family for this agent: `ddflow/api/firstcall.py:42` matches it as a substring of the MCP `clientInfo.name`, so for dsh it should be `dsh` (matches `dsh-mcp-client`). The text in `adding-a-harness.md` that calls `family` "the model-family label used by review independence" does not match the code read (a documentation inaccuracy, filed as `DX-descriptor-family-doc`).

### (d) ddflow into dsh (plugin, skills pack, MCP): EASY for MCP and skills, MODERATE for a package

dsh loads ddflow through what it already has:

- as an **MCP server**: one `dsh-mcp-client` row (stdio `ddflow mcp`, `serverName: ddflow`), which gives `mcp__ddflow__*` tool names the same shape as Claude Code;
- as **skills and instructions**: files in `.agents/skills` and `AGENTS.md`/`CLAUDE.md`, read natively;
- as **hooks**: the Claude-dialect bridge with a ddflow `hooks.json`;
- as a **dsh bundle package** (a Cordis layer: `package.json` with `dsh.profile`/`engines.dsh`, a `cordis.patch.yml`), installed with `dsh plugin --profile <p> add <spec>`: the cleanest one-command path for a user, but it is third-party executable code in the user's profile, the supply-chain surface of an npm package, an `engines.dsh` floor against an unstable API, and ddflow publishing to npm. Held behind the operator's decision (`DSH-bundle`).

A native Cordis plugin (full harness API, typed interception, no `hooks.json` hop) would enforce leases better than the bridge, but it is a TypeScript plugin against an API that promises to break; not proposed.

### (e) Soft upgrade and lean start

- Soft upgrade: `list_changed` is honoured with generation-safe swaps, so a ddflow worker restart that re-announces tools needs no facade for dsh (row above). A stdio crash triggers backoff reconnect, but calls fail during the outage.
- Lean start: dsh registers every MCP tool natively at startup and no deferral mechanism was found, so the full ddflow surface lands in every first turn. The default for `clientInfo.name = dsh-mcp-client` should be the lean profile (`MTD-profiles`, `LS-worker-mcp-config`). Also the instruction baseline is capped at 64 KiB with the **most specific** file kept, so a large root `CLAUDE.md` plus `AGENTS.md` can lose its broader parts silently (a visible notice names the omitted paths, VERIFIED). First-turn tokens: UNKNOWN until measured (`LS-measure`).

## What leaves the machine

dsh sends prompts, file contents and tool output to whatever provider the operator configures (a DeepSeek API, a gateway, or the LAN host). A DeepSeek account sign-in and the account model catalog are optional (`deepseek-account`). Product telemetry is a Desktop-only, switchable consumer (VERIFIED); a web or headless launch collects none by that path. `web-search-deepseek` and `web-fetch-http` are in the base composition, so a model can fetch URLs and send queries unless the operator disables those rows (UNKNOWN: whether they are on by default for headless). The `github-review` webhook plugin and the schedule bundle are opt-in. Session logs may contain credentials the model saw; ddflow's transcript import redacts and is read-only. ddflow itself never triggers any upload.

## Gaps in ddflow found

1. **The descriptor cannot express launch-time configuration.** `[mcp]` is a project file plus a known shape. dsh (and any ACP-injected server, for example `kimi acp`) registers MCP and hooks in a launch overlay, not a project file. Writing `shape = "none"` would be a false "documented absent". Filed as task `DSH-mcp-shape` (generic).
2. **No place to say the config can be silently ignored by omission of a launch flag.** `ddflow verify` and `harness doctor` have only file-based drift; an overlay that was never passed at launch is invisible. Covered by `DSH-onboard-verify` using `--dump-config`; the generic form belongs to `P-agent-lifecycle.verify`.
3. **`adding-a-harness.md` describes `family` as the review-independence label, but the code reads it as a clientInfo-name key** (`firstcall.py:42`); independence uses the model declared at `session start`. Filed as `DX-descriptor-family-doc`.

## Opinion

Support dsh as a **tier-2 harness first** (descriptor, fixtures, delta note) because the facts are verified from source and the descriptor is cheap, then the overlay and the hooks bridge, then a headless lane. Do not build a native Cordis plugin or an npm bundle until the operator asks.

For: DeepSeek AI's own project, MIT, a documented headless `--json`, a standard ACP server, a real MCP client that honours `list_changed`, native `AGENTS.md`/`CLAUDE.md`/`.agents/skills` reading (so most of ddflow's file sync already works), a Claude-compatible hook bridge so the existing ddflow hook normalizer is reused, and a provider layer that talks to this operator's LAN OpenAI-compatible endpoints.

Against, honestly: it is a developer preview that promises breaking changes, so the descriptor will drift and needs a version floor; config lives outside the project in YAML patch layers, so a launch line is part of the integration and a forgotten flag silently removes ddflow; the hooks are an experimental bridge that fails silent on a bad path; the headless profile fails closed on approvals, so unattended lanes force an explicit sandbox decision; ddflow cannot give dsh the same enforcement guarantee as a Claude Code project hook; Node 22.19 is a new runtime dependency for lanes; and nothing here was executed, so every row marked VERIFIED is a documentation or source claim only.

## Stays UNKNOWN

Resolved by `DSH-facts` (see `dsh-probe.md`): hook matcher names and `PreToolUse` shape, `SessionStart` ordering (headless), session file layout (readable with `zstd -dc`), web tools active in headless, first-turn size, MCP tool names. Still unknown:

- Behaviour under a real model and real token counts (probe used a stub).
- Whether any deferred or searched MCP tool exposure exists in dsh outside the docs read.
- Subagent definition files, if any.
- Stability across 0.2.x; whether `engines.dsh` is meaningful for a profile row.
- Windows behaviour (the base composition disables the bash sandbox on win32).
- Whether the npm package makes any network call besides the configured provider.

## Proposed work

Phase `P-dsh-fit`; tasks, needs and waves are in the queue (`ddflow list task --phase P-dsh-fit`), verified acyclic with every need existing.

| Wave | Tasks |
|---|---|
| 1 | `DSH-research` (this document; docs only, not held behind the core and docs wave) |
| 2 | `DSH-facts`, `DSH-mcp-shape`, `DSH-client-row`, `DSH-decide` (carries the hold) |
| 3 | `DSH-descriptor` (dsh appears in the supported list), `DSH-session-ingest` |
| 4 | `DSH-fixtures`, `DSH-overlay` |
| 5 | `DSH-hooks`, `DSH-mcp-profile`, `DSH-onboard-verify`, `DSH-lane`, `DSH-bundle` (optional) |
| 6 | `DSH-lane-acp` |
| 7 | `DSH-e2e` |
| 8 | `DSH-docs` |

Cross-phase edges: `P-agent-parity.launcher`, `P-agent-lifecycle.verify`, `P-agent-lifecycle.manifest`, `MTD-profiles`, `SU-client-table`, `B-am-ingest`, `AC-backend`, `AC-events`, `AC-prefer`.
