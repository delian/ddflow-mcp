# Token-economy and compaction features on every supported coding agent

Task ATF-research (phase P-token-economy), agent res-agent-token-parity, 2026-10-23. Operator request (verbatim, 2026-10-23):
"can you make research for how to manage the same features on other coding agents, not only claude".
Builds on [token-spend.md](token-spend.md), [context-compaction.md](context-compaction.md), [context-compaction-cache.md](context-compaction-cache.md)
(Claude facts and the break-even arithmetic are there and are not repeated). Proposals: [../decisions/agent-token-features.md](../decisions/agent-token-features.md).

Labels: **V** VERIFIED (vendor docs, source or local `--help` read on 2026-10-23), **R** REPORTED (blog, issue, forum, search
snippet), **U** UNKNOWN (looked, not found; where is in the notes). Nothing is guessed. Method caveat: docs were read through a
summarising fetch tool, pages over 100 K characters in parts, so a V cell is "a quoted setting name from the official page", not a
byte-level proof; every V that a launcher will depend on is re-checked by the recorded fixture of `ATF-fixtures`. The local
machine has only `claude` and `kilo` installed (read-only `--help`); no agent was run and no key used. Model names (GPT-6.x, Grok 4.7,
Sonnet 5.5, ...) are as the vendor pages show them today.

## 1. Answer first

1. **The knobs exist for the agents ddflow launches headless, but the names differ and most are per-process config, not env.** Env at launch: Claude
   (`CLAUDE_CODE_AUTO_COMPACT_WINDOW`, `CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL`, `CLAUDE_CODE_PROMPT_CACHE_TTL`), Goose (`GOOSE_CACHE_TTL`,
   `GOOSE_AUTO_COMPACT_THRESHOLD`, ...), Kimi (`KIMI_MODEL_MAX_CONTEXT_SIZE`), Qwen (`QWEN_CODE_MAX_OUTPUT_TOKENS`), Kilo/OpenCode
   (`KILO_CONFIG_CONTENT` / `OPENCODE_CONFIG_CONTENT`, inline JSON config), Aider (`AIDER_*`). Flag at launch: Claude (`--autocompact`,
   `--settings`), Codex (`-c key=value`, repeatable), Copilot (`--additional-mcp-config`, `COPILOT_*`). A lane-private config file or directory is the
   universal fallback (`CODEX_HOME`, `COPILOT_HOME`, `CURSOR_CONFIG_DIR`, `KIMI_SHARE_DIR`, `GROK_HOME`, `CLINE_DIR`, `QWEN_HOME`, `DSH_HOME`).
2. **Cache TTL is a user knob on only three clients**: Claude Code (5m or 1h, main and subagent separately), Goose (`GOOSE_CACHE_TTL`, but always 5m for
   headless and subagents), and the API under Kimi and OpenAI (`prompt_cache_options.ttl`) where the CLI does not expose it (Kimi, Codex: U whether the CLI sets it).
   Everywhere else the cache is automatic and fixed: ddflow can only keep the prefix stable and avoid long idle gaps. Aider alone has keep-alive pings.
3. **Compaction can be steered by text on most agents, replaced on few.** Pre/post compaction events exist on Claude, Codex, Copilot (notification
   only), Cursor (notification only), Gemini (`PreCompress`, advisory), Qwen, Kimi, Grok, and as a plugin hook on Kilo/OpenCode and a Pi extension; the summary can be
   *replaced or its prompt overridden* only on Kilo/OpenCode (`experimental.session.compacting`), Pi (`session_before_compact`), Codex (`compact_prompt`), Roo (custom condensing prompt) and Goose (edit
   the `compaction.md` template). A re-injection after compaction is possible via `SessionStart` with source `compact` on Claude, Codex and Qwen; Goose has a
   per-turn `GOOSE_MOIM_MESSAGE_*` channel that cannot be compacted away.
4. **Limit signals are the weakest area.** A structured, documented signal exists for Claude (`StopFailure` matcher `rate_limit`, stream-json `rate_limit_event`
   (R-usage-quota, binary probe), statusline `rate_limits`), Kimi (print-mode exit code 75 = retryable), Gemini (exit codes 1/42/53, not limits), Codex (`/usage`, rollout
   `rate_limits` R, `x-codex-*` headers R). The rest give message text only (U) or nothing. Reset times are in the message for Claude, in a dashboard for Cursor/Grok/Copilot (not machine-readable).
   Only Claude (`/usage`, statusline), Codex (`/status`, `/usage`), Gemini (`/stats model`), Kimi (`/usage`), Kilo/OpenCode (`stats`, local DB), Amp (`amp usage`) can be queried.
5. **Resume exists nearly everywhere and is flag-shaped** (`--resume`/`--continue`/`resume`/`-S`); the combination "headless + resume" is verified for Claude, Codex (`codex exec resume`), Copilot,
   Cursor, Gemini, Goose, Grok, Kilo/OpenCode, Amp (`threads continue --execute`), Kimi (flags exist; combination U).
6. **Assume the lowest common denominator for an unknown agent** (section 6): automatic cache, 5 minute TTL, compaction at the agent's own threshold which ddflow
   cannot read, no injection after compaction except the next MCP call footer, output uncapped by us, limit seen only as a stop with message text, resume by session id if the
   client gave one, else fresh lane from the resume pack.

## 2. The matrix

Rows are the agents of `ddflow/harnesses/*.toml` plus Pi (research/pi-dev.md) and Antigravity; closed or cloud agents with no descriptor facts here
(cody, devin, glm, openhands, qodo, replit, tabnine, vscode) get one line at the end. Sources per agent are listed in section 8.

### 2.1 Cache control and price ratios (feature 1)

| Agent | TTL / breakpoints / explicit cache | What busts it | Hit / miss ratio (read vs base input) |
|---|---|---|---|
| Claude Code | **V** `CLAUDE_CODE_PROMPT_CACHE_TTL` (`5m`/`1h`, main, v2.1.242+, setting `promptCacheTtl`), `CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL` (non-main: subagents, workflows, background; setting `subagentPromptCacheTtl`), `ENABLE_PROMPT_CACHING_1H=1` (API-key/cloud users; subscriptions get 1h on main automatically), `FORCE_PROMPT_CACHING_5M=1` overrides all; `DISABLE_PROMPT_CACHING[_HAIKU\|_SONNET\|_OPUS\|_FABLE]`; `--exclude-dynamic-system-prompt-sections` moves per-user text out of the prefix; `__SYSTEM_PROMPT_DYNAMIC_BOUNDARY__` line splits a custom system prompt | tools/system/model/fast mode/images change, idle past TTL, compaction (context-compaction-cache.md section 3) | V 0.1x read (0.05x Sonnet/Opus 5.5), write 1.25x (5m) / 2x (1h); `/usage` shows `Prompt cache (main)` hit share and cause (v2.1.251+/260+) |
| Codex CLI | API level, automatic; `prompt_cache_key` optional; GPT-5.6+ `prompt_cache_options.ttl` `30m`/`12h`, earlier `prompt_cache_retention` `in_memory`/`24h`. **U** whether the CLI exposes or sets any of it (no key in the config reference) | model, tools order/schema, reasoning effort, verbosity, compaction, edit of earlier message, expiry | V 0.1x read (0.05x one model); write 1.25x/30m, 2.5x/12h (platform docs) |
| Copilot CLI / VS Code | **U** no user control; backend picks TTL | U | V per-model API rates in billing page: Sonnet 4.6 read $0.30 vs $3.00 (0.1x), cache write $3.75; GPT-5.6 Terra 0.1x; Gemini 4 Argon 0.05x; usage-based billing from 2026-06-01 (R) |
| Cursor | **U** no control; "Cursor models have no cache-write charge" | U | V price table: Sonnet 4.6 read 0.1x of input; Composer 2.5 read $0.2/M (input price not read) |
| Gemini CLI | none in CLI; implicit caching only for API-key and Vertex users, **not** OAuth/Code Assist; min 2048 (2.5) / 4096 tokens; explicit caches exist on the API, not used by the CLI | prefix change | V cached 0.1x (3.1 Pro $2.00 vs $0.20; Flash $0.75 vs $0.075); explicit storage $/M-hour |
| Kimi Code | API (k3): `prompt_cache_options` `{mode: implicit, ttl: 5m\|1h}`, default 5m; Messages API top-level `cache_control`; TTL locked at first write. **U** whether kimi-cli sets it (no cache env var) | prefix change, timestamps/random ids in prefix | V hit 0.1x ($0.30 vs $3.00), write $3.00/M (5m) or $6.00/M (1h), k3 only; K2.x U |
| Qwen Code | `model.generationConfig.enableCacheControl` (bool), automatic for API-key auth, none for OAuth; `context.clearContextOnIdle.toolResultsNumToKeep` / `toolResultsTotalCharsThreshold` clear old tool results "so later turns keep reusing the cache"; TTL **U** | U | explicit cache_control on Model Studio: write 1.25x, hit 0.1x, 5 min (Alibaba docs, from context-compaction-cache.md); implicit hit ~0.2x |
| Goose | **V** `GOOSE_CACHE_TTL` `5m`/`1h` (Anthropic only; 1h writes 2x; **headless `goose run`, subagents and scheduled recipes always 5m**); caching auto-enabled for Claude via Anthropic/Bedrock/Databricks/OpenRouter | not documented | read ratio is Anthropic's (0.1x), not stated by Goose |
| Kilo | **V** automatic: `cacheControl ephemeral` on first 2 system + last 2 non-system messages (Anthropic/OpenRouter/Alibaba), `cachePoint` (Bedrock); provider option `setCacheKey`; **no TTL field sent** (5m); 1h not settable (U) | prune pass ("[Old tool result content cleared]") and compaction rewrite the prefix | Anthropic ratios (not fetched here) |
| OpenCode | **V** (dev branch source) `cache-policy.ts` default `auto` (last tool def, first+last system, tail; max 4) or `none`; `CacheHint.ttlSeconds` exists at library level; user setting for it **U**; `provider.<id>.options.setCacheKey` | U | Anthropic ratios |
| Aider | **V** `--cache-prompts` / `AIDER_CACHE_PROMPTS` (off by default; Sonnet/Haiku, DeepSeek); TTL fixed 5m, **`--cache-keepalive-pings N`** pings every 5 min; `--no-stream` to see stats | history order, changed read-only/editable files, repo map | Anthropic / DeepSeek ratios |
| Cline | **U** (CLI reference, context page silent) | U | n/a |
| Roo | **U** (not read) | U | n/a |
| Amp | server-side, no knob (**U**); usage shows `cache_creation_input_tokens`/`cache_read_input_tokens` in stream-json | U | provider list prices, no markup for individuals (V pricing page) |
| Grok CLI | API automatic prefix cache; header `x-grok-conv-id` maximises hits; no TTL knob (**U**) | changed earlier messages; dropping `reasoning_content` | "reduced cached prompt token price", ratio **U** |
| Windsurf | **U** | U | U |
| dsh | **U** for the harness (provider decides; DeepSeek disk cache automatic, hit $0.003 vs miss $0.15 per M = 2% for flash, V context-compaction-cache.md) | prefix change | per backing model |
| Pi | "cache-preserving by design": changes append a delta message instead of rewriting the system prompt (V pi-dev.md); TTL control **U** | U | per provider |

### 2.2 Auto-compact and window knobs (feature 2)

| Agent | Threshold / window setting | Manual command (custom instructions?) | Larger window / extend |
|---|---|---|---|
| Claude Code | **V** `CLAUDE_CODE_AUTO_COMPACT_WINDOW` (100000-1000000 tokens; `200k` suffix v2.1.304+; beats `/autocompact`, `--autocompact`, setting `autoCompactWindow`), `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` (1-100 % of the window, can only lower), `DISABLE_AUTO_COMPACT=1` (also idle compaction), `DISABLE_COMPACT=1`; subagent frontmatter `autoCompactWindow` (v2.1.296+); default without setting: model limit, ~967K on 1M models | **V** `/compact <text>`; `/autocompact 500k`; `--autocompact <auto\|tokens>` per launch (v2.1.221+) | `[1m]` model suffix (`opus[1m]`, `sonnet[1m]`); `CLAUDE_CODE_DISABLE_1M_CONTEXT=1`; at runtime `/model`, `/autocompact` |
| Codex | **V** `model_auto_compact_token_limit`, `model_auto_compact_token_limit_scope` (`total`/`body_after_prefix`), `model_context_window`; via config.toml or `-c key=value` | `/compact` (instructions **U**); `/status` shows remaining context | model choice only (`-m`); no long-context flag (U) |
| Copilot CLI | **V** auto at ~80 % of window, pause near 95 %; no threshold setting found (**U**). VS Code: `github.copilot.chat.summarizeAgentConversationHistory.enabled` (R) | **V** `/compact [FOCUS-INSTRUCTIONS]` (Esc cancels; irreversible); `/context`, `/session checkpoints` | model dependent; no toggle (U) |
| Cursor | threshold **U**; `maxMode` bool in `~/.cursor/cli-config.json` and `/max-mode` (legacy request-based plans only, +20 %); extended 1M context on Claude/GPT-5.6 models in the model table (V) | `/summarize` (alias `/compress`); instructions **U** | Max Mode / extended context; CLI switch for non-legacy plans **U** |
| Gemini CLI | **V** `model.compressionThreshold` (default 0.5 of context), `experimental.contextManagement`; no window override (**U**) | `/compress` (no argument documented) | model property (Pro >200K tier priced higher) |
| Kimi | **V** `[loop_control]` `compaction_trigger_ratio` (0.85, 0.5-0.99), `reserved_context_size` (50000), whichever first; window per model `max_context_size`, env `KIMI_MODEL_MAX_CONTEXT_SIZE` | **V** `/compact [instructions]`, `/clear` | `[models.X] max_context_size`; long-context switch U |
| Qwen | **V** `context.autoCompactThreshold` (0-1, default 0.85; replaces removed `model.chatCompression.contextPercentageThreshold`), `compactionModel`, `model.sessionTokenLimit`, `QWEN_COMPACT_MAX_RECENT_FILES/IMAGES`; window `model.generationConfig.contextWindowSize` | `/compress` (alias `/summarize`); instructions U (hook input has `custom_instructions`) | settings file only; no env for the window |
| Goose | **V** `GOOSE_AUTO_COMPACT_THRESHOLD` (0-1, default 0.8, 0 disables), `GOOSE_AUTO_COMPACT_TOKEN_LIMIT` (default 225000); trigger = min of the two; `GOOSE_CONTEXT_LIMIT` (window override, default 128000), `GOOSE_TOOL_CALL_CUTOFF` | `/compact` (no argument documented) | `GOOSE_CONTEXT_LIMIT` |
| Kilo | **V** `compaction.auto` (true), `compaction.threshold_percent`, `compaction.prune` (true, 40K recency), `compaction.tail_turns` (2), `compaction.preserve_recent_tokens`, `compaction.reserved`; env `KILO_DISABLE_AUTOCOMPACT`, `KILO_DISABLE_PRUNE`; `agent.compaction.model` | `/compact` (aliases smol, condense); instructions none documented | `provider.<id>.models.<m>.limit.context`; models with no declared window are never auto-compacted |
| OpenCode | **V** `compaction.auto`, `compaction.prune` (docs default false), `compaction.reserved`; dev schema `compaction.buffer`, `compaction.keep.tokens`; env `OPENCODE_DISABLE_AUTOCOMPACT`, `OPENCODE_DISABLE_PRUNE` | `/compact` (alias `/summarize`, R); instructions U | `limit.context/limit.output` per custom model |
| Aider | **V** no whole-context compaction; history summarised by the weak model above `--max-chat-history-tokens` (default min(max(max_input/16,1024),8192)) | `/clear`, `/reset`, `/tokens` (no `/compact`) | `--model-metadata-file` |
| Cline | **V** `--compaction agentic\|basic\|off` (default agentic); threshold U | `/smol` (alias `/compact`); instructions U | U |
| Roo | **V** settings "Automatically trigger intelligent context condensing" (default on) and threshold slider (percent, default 100) | "Condense Context" button; custom prompt (section 2.3) | U |
| Amp | automatic, by a supporting model, for main thread and subagents alike; thresholds **U**; alternative "handoff" to a new thread | none found (U) | n/a |
| Grok CLI | **V** `[session] auto_compact_threshold_percent` (0-100, default 85), `[model.<id>] context_window` (BYOK) | `/compact` **U** | BYOK window |
| Windsurf | U | U | U |
| dsh | **V** `compaction-basic`, `/compact`, tool-result pruner (apps/cli/composition.md); per-model context window in `llm-pi-ai` config; threshold setting **U** | `/compact` | per-model `input`/window in profile patch |
| Pi | **V** automatic near the limit, summary entry added, originals kept; `compact` RPC command | `/compact` / RPC `compact` | provider/model choice |

### 2.3 Compaction hooks, summary replacement, instructions that survive (feature 3)

| Agent | Hooks around compaction | Custom prompt / replace summary | Instruction file and survival |
|---|---|---|---|
| Claude Code | **V** `PreCompact` (matcher `manual`/`auto`; may block via exit 2 or `decision`), `PostCompact` (no control), `SessionStart` source `compact` (injects `additionalContext`); resume payload has `seconds_since_last_response`, `context_tokens`, `prompt_cache_likely_expired`, `estimated_cache_write_usd` | no replacement; `/compact <text>`; hook context is itself summarised away (context-compaction.md) | `CLAUDE.md` (+ AGENTS.md conditional), **`# Compact instructions` section is honoured (V costs page)**; that CLAUDE.md is re-read after compaction is the documented design (system layer stays), not separately probed (U) |
| Codex | **V** `PreCompact`/`PostCompact` (matcher `trigger`, stdout ignored), `SessionStart` matcher `source: "compact"` (stdout / `additionalContext` becomes developer context) | **V** `compact_prompt`, `experimental_compact_prompt_file`; replacement U; server compaction is opaque | `AGENTS.override.md` > `AGENTS.md` > `project_doc_fallback_filenames`, root to cwd, `project_doc_max_bytes` 32 KiB (R); `developer_instructions`, `model_instructions_file` (V); reload after compaction U; no "Compact instructions" analogue (U) |
| Copilot | **V** `preCompact` (alias `PreCompact`): "No - notification only"; no PostCompact, no SessionStart(compact) (**U**) | none (focus text on `/compact` only) | `copilot-instructions.md`, `AGENTS.md`, `CLAUDE.md` (V names, exact paths U); custom instructions are part of the system prompt (`/context` slice) |
| Cursor | **V** `preCompact` ("cannot block or modify", user_message only); no post (U) | none (U) | `.cursor/rules/*.mdc` (`alwaysApply: true`), `AGENTS.md`, User Rules; survival through summary U |
| Gemini CLI | **V** `PreCompress` (trigger auto/manual; `systemMessage` only, "cannot block or modify"); no post | none found (U) | `GEMINI.md` (global, workspace, parents, JIT), `context.fileName` list e.g. `["AGENTS.md","GEMINI.md"]`; sent with each prompt (so survives, R by design) |
| Kimi | **V** `PreCompact`, `PostCompact` exist (`[[hooks]]` in config.toml, Beta); capabilities U | none found (U) | `AGENTS.md` root to cwd + `.kimi/AGENTS.md`, in the system prompt each turn (inference: survives); subagents U |
| Qwen | **V** `PreCompact` (input `custom_instructions`; may add `additionalContext` before compaction), `PostCompact` (input `compact_summary`; output ignored), `SessionStart` source `compact`, `InstructionsLoaded` | custom prompt U; no replacement | `QWEN.md`, `context.fileName` list (AGENTS.md only if listed, U default) |
| Goose | **none** (no PreCompact/PostCompact in the hooks event list) | **V** edit `compaction.md` template (prompt templates) | `AGENTS.md`, `.goosehints`, `CONTEXT_FILE_NAMES`; **`GOOSE_MOIM_MESSAGE_TEXT` / `_FILE` (<= 64 KB) injected every turn, survives compaction** |
| Kilo | plugin hook **V** `experimental.session.compacting` (`output.context[]` append, `output.prompt` replace), `experimental.compaction.autocontinue`; no post hook | **V** replace prompt via plugin | `AGENTS.md`, `instructions` array in kilo.json; re-sent each turn (survival inferred; U stated) |
| OpenCode | **V** same plugin hook, event `session.compacted` | **V** replace via plugin | `AGENTS.md` (CLAUDE.md fallback), `~/.config/opencode/AGENTS.md`, `instructions`; survival U |
| Aider | none | none; `--read CONVENTIONS.md` is read-only, cached, never summarised | `--read`, `.aider.conf.yml` `read:`; AGENTS.md auto-load U |
| Cline | SDK plugin hooks `beforeRun/afterRun/beforeTool/beforeModel`; no compaction hook documented (U) | none (U) | `.clinerules` / `.cline/` (detail U) |
| Roo | none documented | **V** Settings > Context Management > "Custom Context Condensing Prompt" (`customSupportPrompts.CONDENSE`); slash commands in the first message survive | `.roo/rules`, AGENTS.md: U |
| Amp | plugin API events `session.start`, `tool.call`, `tool.result`, `agent.start`, `agent.end`; no compaction event found (U) | none | `AGENTS.md` cwd/parents/`~/.config/amp/AGENTS.md`/`/etc/ampcode/AGENTS.md` (V); survival U |
| Grok CLI | **V** `PreCompact`, `PostCompact` events (influence on summary U) | U | AGENTS.md, CLAUDE.md, `.grok/rules/*.md`, `--rules <TEXT>` |
| Windsurf | none | none | `.devin/rules` (legacy `.windsurf/rules`), AGENTS.md as rules |
| dsh | **none** (bridges have no pre-compaction event, V dsh.md) | none | `AGENTS.md`/`CLAUDE.md` per directory, 65 536-byte budget |
| Pi | **V** extension event `session_before_compact` (may supply the summary), `agent_before_settle` | **V** extension supplies the summary; originals kept | `AGENTS.md`, `AGENTS.override.md`, `CLAUDE.md`; `--no-context-files` |

### 2.4 Output caps and subagents (features 4 and 5)

| Agent | Bound tool/shell/MCP output; max tokens; brief mode | Subagent: isolated context, summary back, model, per-lane env |
|---|---|---|
| Claude Code | **V** `BASH_MAX_OUTPUT_LENGTH` (30000 chars, max 150000; setting `bashOutputMaxChars`), `MAX_MCP_OUTPUT_TOKENS` (25000; warn at 10 000; >50 000 chars saved to a file; per-tool `anthropic/maxResultSizeChars`), `CLAUDE_CODE_MAX_OUTPUT_TOKENS`, `MAX_THINKING_TOKENS`, `/effort`; PostToolUse `updatedToolOutput`, PreToolUse `updatedInput` (rewrite the command) | **V** own context, "returns only the summary"; model: Agent `model` param > frontmatter `model` > `CLAUDE_CODE_SUBAGENT_MODEL` (`..._FORCE`) > parent; frontmatter `autoCompactWindow`, `effort`, `maxTurns`, `hooks`, `mcpServers`; **no `env` frontmatter**, so per-lane env is the launch environment of the whole process |
| Codex | **V** `tool_output_token_limit`, per-MCP-tool `mcp_servers.<id>.tools.<tool>.output_token_limit`, `tool_timeout_sec`, hook `additionalContextLimit` (2500 tokens), `model_verbosity=low`; max output tokens U | **V** config `agents.enabled`, `agents.default_subagent_model`, `agents.default_subagent_reasoning_effort`, `agents.<name>.config_file`, `agents.max_concurrent_threads_per_session`; isolation vs fork **U/R conflicting**; per-lane: `CODEX_HOME`, `-p profile`, `-c` |
| Copilot | **V** `COPILOT_LARGE_OUTPUT_THRESHOLD_BYTES` (20480; larger goes to a temp file with a preview), `/limits set max-ai-credits`, `--effort`, `-s/--silent`; MCP output limit U | **V** "separate context windows"; frontmatter `model`, `models`, `modelPolicy`, `reasoningEffort`; settings `subagents.maxConcurrency/maxDepth`; per-lane `COPILOT_HOME`, `COPILOT_MODEL`; summary-only return U |
| Cursor | none documented (U) | **V** own context, "parent only sees the final summary"; frontmatter `model` (`inherit`), `readonly`, `is_background`; reads `.cursor/agents`, `.claude/agents`, `.codex/agents`; per-lane `CURSOR_CONFIG_DIR` |
| Gemini CLI | **V** `tools.truncateToolOutputThreshold` (40000 chars), `model.maxSessionTurns`; max output tokens U; MCP limit U | **V** independent context, findings back; `.gemini/agents/*.md` frontmatter `model`, `max_turns` 30, `timeout_mins` 10; `agents.overrides.<name>.modelConfig.model` |
| Kimi | **V** `KIMI_MODEL_MAX_COMPLETION_TOKENS`, `--max-steps-per-turn`, `--final-message-only`; tool-output truncation U | **V** `Agent` tool (`coder`/`explore`/`plan`), own context under `subagents/<id>/`, optional `model` per launch; summary step not documented; per-subagent env U |
| Qwen | **V** `tools.truncateToolOutputThreshold`, `tools.truncateToolOutputLines` (defaults U), `QWEN_CODE_MAX_OUTPUT_TOKENS`, `--max-session-turns/--max-wall-time/--max-tool-calls`, `--output-style Concise`; MCP limit U | **V** separate context; frontmatter `model: inherit\|fast\|<id>`; `agents.builtin.exploreModel`, `model.maxSubagentDepth`; fork subagents inherit parent context |
| Goose | **V** `GOOSE_MAX_TOKENS`, `GOOSE_MAX_TOOL_RESPONSE_SIZE` (200000 chars; larger goes to a temp file), `GOOSE_TOOL_CALL_CUTOFF`, `GOOSE_MAX_TURNS`, `GOOSE_SUBAGENT_MAX_TURNS` | **V** isolated instances returning `execution_summary`; `GOOSE_SUBAGENT_PROVIDER`, `GOOSE_SUBAGENT_MODEL`, `GOOSE_MAX_CONCURRENT_SUBAGENTS` (5); cache TTL forced 5m in subagents |
| Kilo | **V** `tool_output.max_lines` (2000) / `max_bytes` (51200, read in source, schema U), overflow saved to a file for 7 days; `KILO_EXPERIMENTAL_OUTPUT_TOKEN_MAX`; MCP limit U | child sessions via Task tool; `agent.<name>` `mode: subagent`, `model`, `steps`; summary return R; per-lane `KILO_CONFIG_CONTENT`, `KILO_CONFIG`, `KILO_CONFIG_DIR` |
| OpenCode | `tool_output.*` (same lineage, not on the config doc), `OPENCODE_EXPERIMENTAL_OUTPUT_TOKEN_MAX` | General/Explore/Scout subagents, per-agent `model`, `subagent_depth` 1, per-lane `OPENCODE_CONFIG`, `OPENCODE_CONFIG_DIR`, `OPENCODE_CONFIG_CONTENT` |
| Aider | `--map-tokens`, `--timeout`; no truncation, no MCP | none; `--weak-model` (summaries, commits), `--editor-model` |
| Cline | `-t/--timeout`, `--retries`; caps U | **V** `spawn_agent`: separate context, final reply returned, no MCP tools inside; model U; per-lane `CLINE_DIR`, `--config`, `CLINE_DATA_DIR` |
| Roo | U | U |
| Amp | U (`amp.tools.disable` drops tools) | **V** Task subagent and Read Thread: own context, main agent receives the final summary; same model and effort as the thread; plugin agents take `model` |
| Grok CLI | **V** `[toolset.bash] output_byte_limit` (20000) | **V** "independent child sessions ... return a summary to the parent"; `.grok/agents`; `GROK_SUBAGENTS=0`; per-subagent model U; per-lane `GROK_HOME`, `GROK_DEFAULT_MODEL` |
| Windsurf | U | U |
| dsh | stdout/event lines capped by the `--json` protocol (32 KiB lines); tool limits U | native tools, definition files not found; MCP stdio env scrubbed of `DSH_*` and `*KEY*`/`*TOKEN*` names |
| Pi | U | extensions; no built-in subagents (pi-dev.md); `tool_search`/codemode tiers cut tool schema size |

### 2.5 Spend and limit awareness, resume (feature 6)

| Agent | Limit signal and reset | Query remaining quota | Resume (headless where noted) |
|---|---|---|---|
| Claude Code | **V** messages `You've hit your session limit` / `weekly limit` / `Opus limit` / `Sonnet limit` (reset time in the text, e.g. `limit resets 3:45pm`), `monthly spend limit`, `Request rejected (429)`; hook **`StopFailure`** matcher `rate_limit` (also `billing_error`, `overloaded`, `max_output_tokens`); stream-json `rate_limit_event` with utilization + `resets_at` for 5-hour and 7-day windows (R-usage-quota, binary probe 2.1.283); auto-wait and continue after reset (`autoContinueAtUsageLimit`, v2.1.234+), `CLAUDE_CODE_RETRY_WATCHDOG=1` for unattended retry; exit code on limit **U** | **V** `/usage` (bars, `Showing last-known usage`), statusline `rate_limits`, `~/.claude.json` `cachedUsageUtilization` (stale, R-usage-quota), OTel, Admin APIs | **V** `--resume <id\|name>`, `-c`, `--session-id`, `--fork-session`, `-p --continue`; `--max-budget-usd`, `--max-turns` |
| Codex | `You've hit your usage limit` (R); 5-hour and weekly windows; exit code U | **V** `/usage [daily\|weekly\|cumulative]`, `/status`, `/statusline` rate limits; R rollout files `~/.codex/sessions` `token_count.rate_limits`, headers `x-codex-primary-used-percent`, `GET /api/codex/usage` | **V** `codex resume [ID] [--last]`, `codex exec resume [--last] [ID] [prompt]`; `--ephemeral` disables rollout files |
| Copilot | session (5h?) and weekly limits (R, pages disagree), AI-credit allowance; text, exit code U | `footer.showQuota` (R), `/usage` is per-session metrics only (V); no API (U) | **V** `--continue`, `-r/--resume[=VALUE]`, `--session-id`, `COPILOT_ENABLE_INTERRUPTED_SESSION_RESTORE=1` |
| Cursor | weekly pool shared by Agent/CLI/Cloud; reset date shown in settings; "requests are never downgraded" (V); text, exit code U | editor settings only; CLI command U | **V** `--resume [chatId]`, `--continue`, `/fork` |
| Gemini CLI | daily request caps by tier (1000 / 1500 / 2000 / 250), reset time U; 429 text U; fallback-to-Flash on 429 is string-matched (R #13112); **exit codes 0, 1, 42 input, 53 turn limit** (not limit) | **V** `/stats model` | **V** `--resume/-r latest\|index`, `/resume`, `/chat` checkpoints |
| Kimi | **V print-mode exit 75 = retryable (429, 5xx, timeout), 1 = permanent**; quota refreshes every 7 days plus a rolling 5-hour window (R, wording ambiguous); text U | **V** `/usage` (alias `/status`, Kimi Code platform only) | **V** `--continue/-C`, `--session/-S <id>`, `--resume/-r`, `/fork`; print + resume U |
| Qwen | `QWEN_CODE_UNATTENDED_RETRY=1` keeps a headless run alive through 429/529 (V); text U; exit 53 turn limit, 55 budget | `/stats` (alias `/usage`) token stats only; plan quota via provider console | **V** `--continue`, `--resume [id]`, `/resume` |
| Goose | none built in (V guide recommends routers that auto-switch); text U | `GOOSE_CLI_SHOW_COST` estimates only | **V** `goose session --resume [-n name\|--session-id id]`, `goose run --resume`, `--fork` |
| Kilo | regex on `rate limit\|too many requests\|try again later` + `retry-after(-ms)` headers, capped backoff, `KILO_SESSION_RETRY_LIMIT`; **`kilo run` exit 0 / 124 timeout / 1 error** | **V** `kilo stats`, `kilo session list`, `kilo export`, local DB `KILO_DB` | **V** `-c`, `-s <id>`, `--fork` |
| OpenCode | same lineage (retry on text and headers); exit codes U | **V** `opencode stats`, `export` | **V** `run -c/-s/--fork` |
| Aider | litellm `RateLimitError` message "The API provider has rate limited you. Try again later or check your quotas."; retry 0.125 s doubling, gives up after 60 s; exit code U | none; cost shown per message | `--restore-chat-history`, `--chat-history-file` (not a session resume) |
| Cline | error as `{"type":"error",...}` on stderr; exit codes "not documented" (V) | `usage` events and `run_result.usage` in JSON | **V** `--id <session-id>`, `cline history --json` |
| Roo | U | U | U |
| Amp | credits, monthly tier resets at period end (V); text, exit code U | **V** `amp usage` | **V** `amp threads continue T-... [--execute --stream-json]` |
| Grok CLI | weekly pool, next reset date in the usage view (V); text U | no CLI command found (U) | **V** `-r/--resume [ID]`, `-c`, `-s <uuid>`, `--fork-session`, `grok sessions list` |
| Windsurf | U | U | U |
| dsh | exit 0 done, 1 aborted/errored (V); a `status` event exists in `--json`; limit text U | U | **V** resume by id; ACP `session/resume` |
| Pi | U | U | RPC `get_state`, `fork`; sessions JSONL (V pi-dev.md); resume flag U |

### 2.6 Injection channels and routing (features 7 and 8)

| Agent | Hook door that reaches the model (V unless noted) | Cheaper model per run/lane; headless line; MCP at launch |
|---|---|---|
| Claude Code | `SessionStart`, `UserPromptSubmit`, `PostToolUse`, `SubagentStart`, `SubagentStop` accept `additionalContext`; `PreCompact` and `PostCompact` do not; files `CLAUDE.md`, `.claude/rules/*.md`, `.claude/settings.json` | `--model`, `ANTHROPIC_MODEL`, `ANTHROPIC_DEFAULT_{OPUS,SONNET,HAIKU,FABLE}_MODEL`, `CLAUDE_CODE_SUBAGENT_MODEL`, frontmatter `model`, `--effort`, `--fallback-model`; `claude -p --output-format stream-json --settings <file\|json> --mcp-config <f> --strict-mcp-config --bare` |
| Codex | `SessionStart`, `UserPromptSubmit` stdout/additionalContext, `PostToolUse` additionalContext (plain stdout ignored); `[hooks]` in `config.toml`/`hooks.json`, `features.hooks` | `-m`, `-p profile` (`$CODEX_HOME/<name>.config.toml`), `review_model`, `agents.default_subagent_model`; `codex exec --json`, `-c`; MCP via `codex mcp add` (config.toml) or `-c mcp_servers.<id>...` (**inferred, U**) |
| Copilot | `sessionStart`, `postToolUse`, `postToolUseFailure`, `subagentStart`, `notification` inject; `preToolUse`, `userPromptSubmitted`, `preCompact` do not; `.github/hooks/*.json`, `~/.copilot/hooks/`, Claude-compatible `.claude/settings.json` | `--model` (accepts `auto`), `COPILOT_MODEL`, `/model`, `--effort`; `copilot -p --output-format json -s --no-ask-user --additional-mcp-config @file` |
| Cursor | `additional_context` from `sessionStart` (fire-and-forget), `postToolUse`, `postToolUseFailure`; `.cursor/hooks.json`; hooks in print mode **U** | `--model`, `/model`, Auto Cost/Balance/Intelligence, subagent `model`; `agent -p --output-format stream-json -f`; no per-launch MCP flag (U) |
| Gemini CLI | `SessionStart` ("first turn in history"), `BeforeAgent` (this turn only), `AfterTool` additionalContext; `settings.json` `hooks` | `-m`, `model.name`, `/model set`; `gemini -p -o stream-json`; MCP only through `settings.json` (worktree-local `.gemini/settings.json`, inference) |
| Kimi | hook stdout "added to context" (event-specific U); exit 2 stderr fed to the model; `[[hooks]]` | `-m`, `KIMI_MODEL_NAME`, `--config-file`; `kimi --print --output-format stream-json --mcp-config-file <f>` |
| Qwen | plain stdout of `SessionStart`/`UserPromptSubmit`/`UserPromptExpansion` and `additionalContext` of PreToolUse/PostToolUseFailure/PostToolBatch/UserPromptSubmit/SessionStart/PreCompact; hook input has `context_usage`, `context_limit` | `-m`, `fastModel`, `compactionModel`, `agents.builtin.exploreModel`; `qwen -p -o stream-json --approval-mode`; MCP in settings only (U flag) |
| Goose | hook stdout injection **U**; reliable channel `GOOSE_MOIM_MESSAGE_TEXT/_FILE` every turn | `GOOSE_MODEL`, `GOOSE_SUBAGENT_MODEL`, recipes; `goose run -t --output-format stream-json --max-turns`; `--with-extension`, `--with-streamable-http-extension` |
| Kilo | plugin hooks `experimental.chat.system.transform`, `chat.message`, `tool.execute.after` (R/V source); no shell hook config; `AGENTS.md`, `instructions` | `-m prov/model`, `small_model`, per-agent `model`, `--variant`; `kilo run --format json --auto`; `KILO_CONFIG_CONTENT` for MCP |
| OpenCode | same plugin hooks (docs deny confirming `chat.*`); `AGENTS.md`, `instructions` | `-m`, `small_model`; `opencode run --format json --auto`; `OPENCODE_CONFIG_CONTENT` |
| Aider | none; `--read`, `/run`, `--message-file` | `--model`, `--weak-model`, `--editor-model`, `AIDER_*`; `aider -m ... --yes-always`; no MCP |
| Cline | hook output to model **U**; `-s/--system` | `-m`, `-P`, `--thinking`; `cline --json --auto-approve`; MCP file `CLINE_MCP_SETTINGS_PATH` |
| Roo | U | U |
| Amp | `agent.start` hook returns `message` appended to the user message; `agent.end` can return `continue` + `userMessage`; `tool.result` can replace output; need `--plugin-ready-timeout` in `-x` mode | modes `low/medium/high/ultra`, `--fast`; `amp -x --stream-json`; `--mcp-config '{...}'` |
| Grok CLI | **no documented injection**: passive-hook stdout ignored; `--rules <TEXT>`, `--append-system-prompt`, `.grok/rules/*.md` | `-m`, `GROK_DEFAULT_MODEL`, `--effort`; `grok -p --output-format streaming-json --always-approve`; MCP only via config |
| Windsurf | injection not documented; hooks (`pre_user_prompt`, ...) at `.devin/hooks.json` | U |
| dsh | `SessionStart` (context, runs detached but arrived before turn 1 in the probe), `UserPromptSubmit`, `PostToolUse`; no pre-compact | `session/set_config_option` (model, reasoning_effort) over ACP; `dsh --profile headless --json`; MCP only via a Cordis patch overlay `--patch` |
| Pi | extension events (`input`, `context`, `before_provider_request`, `tool_result`); `AGENTS.md` | provider/model in `models.json`; `pi --mode rpc\|json`, `--print`; MCP `pi mcp add` with `exposure: direct` |

Closed or cloud agents without descriptor-level facts here (cody, devin, glm, openhands, qodo, replit, tabnine, vscode, Antigravity): **U** for every row.
Antigravity was not researched (no search was run); OpenHands has a documented condenser (research/openhands.md); treat all as "no control".

## 3. What ddflow can do per agent

Levels: **LAUNCH** (ddflow sets it for a lane it starts: env, flag, `-c`, lane-private config dir), **INJECT** (text reaches the model through a door),
**QUERY** (ddflow can read usage or limit state), **ADVISE** (only the operator or the model can act), **NONE**. Lane-private config files
are written in the lane's own directory, never in the operator's user config (D-agent-parity: nothing is installed or edited without consent).

| Agent | Cache TTL | Compaction window / threshold | Compact action | Re-inject after compaction | Limit signal | Resume | Cheaper model per lane |
|---|---|---|---|---|---|---|---|
| Claude Code | LAUNCH env | LAUNCH env/flag (`--autocompact`, subagent frontmatter) | ADVISE (`/compact <text>`), text via `# Compact instructions` | INJECT `SessionStart:compact` | QUERY hook `StopFailure`, stream event, statusline | LAUNCH `--resume` | LAUNCH flag/env |
| Codex | NONE (U) | LAUNCH `-c` | ADVISE; LAUNCH `compact_prompt` | INJECT `SessionStart:compact` | QUERY (rollout R, `/usage`) | LAUNCH `exec resume` | LAUNCH `-m`, `-p` |
| Copilot CLI | NONE | NONE (fixed 80 %) | ADVISE `/compact <focus>` | INJECT `sessionStart` only at start; none after | ADVISE (text U) | LAUNCH `--resume` | LAUNCH `--model` |
| Cursor | NONE | NONE | ADVISE `/summarize` | none documented | ADVISE | LAUNCH `--resume` | LAUNCH `--model` |
| Gemini CLI | NONE | LAUNCH (settings file in the lane's `.gemini/`) | ADVISE `/compress` | none (PreCompress advisory) | QUERY exit 1; `/stats` | LAUNCH `--resume` | LAUNCH `-m` |
| Kimi | NONE (U) | LAUNCH env `KIMI_MODEL_MAX_CONTEXT_SIZE`, `--config-file` ratio | ADVISE `/compact <text>` | PostCompact hook (capabilities U) | QUERY **exit 75**; `/usage` | LAUNCH `-S` | LAUNCH `-m`, env |
| Qwen | NONE | LAUNCH settings (`QWEN_HOME`) | ADVISE `/compress` | INJECT `SessionStart:compact` | ADVISE; `QWEN_CODE_UNATTENDED_RETRY` | LAUNCH `--resume` | LAUNCH `-m`, settings |
| Goose | LAUNCH env (interactive only; headless forced 5m) | LAUNCH env | ADVISE `/compact`; LAUNCH template | INJECT `GOOSE_MOIM_MESSAGE_*` every turn | ADVISE | LAUNCH `--resume` | LAUNCH env |
| Kilo | NONE (5m fixed) | LAUNCH `KILO_CONFIG_CONTENT` | ADVISE; plugin can replace prompt | plugin `experimental.session.compacting` | QUERY exit codes, `kilo stats` | LAUNCH `-s` | LAUNCH `-m`, config |
| OpenCode | NONE | LAUNCH `OPENCODE_CONFIG_CONTENT` | ADVISE; plugin | plugin | QUERY `stats` | LAUNCH `-s` | LAUNCH `-m` |
| Aider | LAUNCH `--cache-prompts`, keep-alive pings | LAUNCH `--max-chat-history-tokens` | none | `--read` file (never summarised) | ADVISE | no session resume | LAUNCH `--model`, `--weak-model` |
| Cline | NONE | LAUNCH `--compaction` | ADVISE `/smol` | none | ADVISE | LAUNCH `--id` | LAUNCH `-m` |
| Roo | NONE | settings (IDE) | ADVISE button | none | NONE | NONE | settings |
| Amp | NONE | NONE | NONE (automatic) | INJECT `agent.start` plugin | QUERY `amp usage` | LAUNCH `threads continue` | modes |
| Grok CLI | NONE | LAUNCH config.toml (`GROK_HOME`) | ADVISE (command U) | none | ADVISE | LAUNCH `--resume` | LAUNCH `-m` |
| Windsurf, closed IDEs | NONE | NONE | ADVISE | NONE | NONE | NONE | NONE |
| dsh | NONE | LAUNCH profile patch | ADVISE `/compact` | INJECT `SessionStart` (no compact event) | exit 1 only | LAUNCH resume / ACP | ACP `set_config_option` |
| Pi | NONE | provider config | ADVISE; extension supplies summary | extension | NONE | RPC | provider config |

## 4. Gaps (what ddflow cannot do, and what to file)

1. **No cache-TTL control** on Codex, Copilot, Cursor, Gemini, Kimi (CLI), Qwen, Kilo, OpenCode, Cline, Roo, Amp, Grok, Windsurf, dsh, Pi. ddflow can only keep tools/instructions byte-stable (CC-cache-policy) and keep gaps short (TE-poll-cadence); the 1 h TTL saving of D-TE-Q1 does **not** transfer. Goose's knob is ignored in headless.
2. **No compaction trigger from outside** on every agent except Pi RPC (`compact`); Kilo/OpenCode plugin could expose one (not built). Rotation (fresh lane from the resume pack) is the portable action, as D-CC-2 already says.
3. **No re-injection door after compaction** on Copilot, Cursor, Gemini, Kimi (U), Grok, Aider, Cline, Roo, Windsurf, dsh. Fallbacks in order: the footer on the next ddflow MCP call (`[reinstruct]`), the instruction file (always re-sent on every request on all clients that rebuild the system prompt), `GOOSE_MOIM_MESSAGE_FILE` on Goose.
4. **Limit text and exit codes are U** for Claude (exit), Codex, Copilot, Cursor, Gemini, Qwen, Goose, OpenCode, Aider, Cline, Roo, Amp, Grok, Windsurf, dsh. These need recorded fixtures from a real limit (cannot be produced without spending a limit): ATF-fixtures records what can be recorded (Kimi 75, Kilo 124/1, Gemini 53) and marks the rest `unavailable`, never a pass.
5. **Docs conflict** worth a probe: Kilo `preserve_recent_tokens` (docs 8 000 vs source 15 000), OpenCode `compaction.prune` (docs false vs Kilo true), Copilot session/weekly limit pages (es vs en), `--yes` vs `--yes-always` in Aider, Cursor binary name (`agent` vs `cursor-agent`), Codex subagent isolation (fork vs summary).
6. **Claude-only advisor inputs** (`prompt_cache_likely_expired`, `estimated_cache_write_usd`, transcript `usage`) have no counterpart elsewhere except Qwen hook input (`context_usage`, `context_limit`, `input_tokens`), Codex rollout `token_count` (R), Cline `usage` events, Amp stream-json `cache_*` tokens and Kilo/OpenCode `stats`/DB. Elsewhere context size is an estimate.
7. File as defects (D-file-ddflow-defects): the `kimi` descriptor still describes kimi-cli (R-kimi, existing P-agent-parity.kimi-descriptor); `adding-a-harness.md` lacks every field proposed below (ATF-descriptor-fields).

## 5. Proposed descriptor extension (schema, reuse of existing vocabulary)

Existing vocabulary to reuse, not duplicate: three-valued rule and `[verified]` (adding-a-harness.md); HX-facts-core fact record `{value, status, source, tool_version, date}`
(status `verified|documented|unverified|none`); sections reserved by P-agent-parity.descriptor-v2: `[headless]` (argv template, model/effort flags, MCP injection, **resume flag**, output format, exit codes), `[meters]` (context and **quota sources**), `[caps]` (mcp tool cap, instructions cap, subagent depth), `[continuation]`, `[spawn]` (subagent model/definition files), `[transcripts]`, `[hooks.events]` (canonical events; DX-descriptor-vocab-dsh adds subagent events), `[env]`. the planned task CC-inventory (blocked on CC-decide; the file does not exist yet) will keep **model-family** facts (cache multipliers, window) in its declared glob `ddflow/templates/context_families.toml` and says it does not edit `harnesses/*.toml`; this proposal fills only **harness** facts the launcher and advisor need.

Placement (no parallel structure):

| Fact | Where | Why |
|---|---|---|
| resume argv, model flag, output format, MCP injection form | `[headless]` (descriptor-v2) | already reserved: `resume = ["--resume", "{session}"]` |
| quota/limit signal and query command | `[meters]` (descriptor-v2): new keys `limit_signal`, `limit_exit_code`, `limit_match`, `reset_source`, `query` | descriptor-v2 reserves `[meters]` for quota sources; these are its sub-keys, one definition |
| output caps (tool output, MCP, shell) | `[caps]` (descriptor-v2): new keys `tool_output`, `mcp_output`, `shell_output`, `max_completion` | `[caps]` already holds caps |
| `post_compact`, `session_start_compact` canonical events | `[hooks.events]` + CANONICAL_EVENTS, in DX-descriptor-vocab-dsh | the same vocabulary task already adds subagent events |
| per-subagent model / window | `[spawn]` (descriptor-v2): keys `model_setting`, `window_setting` | subagent facts live with `[spawn]` |
| **cache TTL, compaction window, compaction commands/instructions** | **new section `[tokens]`** (one definition, registered in descriptor-v2 by amendment) | nothing reserved covers these |

`[tokens]` keys (each is a value, `none = true`, or absent = not verified; a value may carry the HX-facts-core fact record):

```
[tokens.cache]       control = "ttl-env" | "ttl-setting" | "automatic" | "keepalive" | "none"
                     ttl_values = ["5m","1h"]; default_ttl = "5m"
                     set = {main = <setter>, subagent = <setter>}     # a setter is the launch mechanism below
                     headless_ttl_fixed = "5m"                        # Goose: headless forces 5m
[tokens.window]      set = <setter>  unit = "tokens" | "ratio" | "percent"  min = .. max = ..
                     threshold = <setter>  unit = ..                  # trigger point when separate from the window
                     default_trigger = "967k" | 0.85 | ...            # documented default
[tokens.compact]     command = "/compact"  takes_instructions = true | false | (absent)
                     instructions_region = "# Compact instructions" | none = true
                     prompt_setting = <setter>                        # codex compact_prompt, roo CONDENSE
[tokens.output]      brief = <setter>                                 # --silent, --output-style Concise, model_verbosity=low (the only home of `brief`)
[tokens.lane_dir]    env = "CODEX_HOME" | argv = ["--config-dir", "{dir}"]   # how the launcher relocates the agent's config dir for one lane; a `file` setter is relative to it
```

A **setter** is one inline table naming how a launcher applies a value, reusing the launch vocabulary of `[headless]`/the launcher (`P-agent-parity.launcher`: env, argv, lane-private file):
`{env = "NAME", value = "{tokens}"}`, `{argv = ["-c", "model_auto_compact_token_limit={tokens}"]}`, `{file = "kimi.config.toml", key = "loop_control.compaction_trigger_ratio", value = "{ratio}"}`
(`file` is relative to the lane's private config dir named by `[tokens.lane_dir]`, e.g. `env = "CODEX_HOME"`; absent = no lane-private file is possible and file setters are reported unavailable). Placeholders are `{tokens}`, `{ratio}`, `{percent}`, `{ttl}`.
Validation: unknown key = loader error naming key and file (existing rule); a setter without `[verified]` source is shown as `not verified` by `ddflow harness show` and the launcher refuses to apply it
(prints `unavailable`, never silently skips). Every value is overridable in config (D-everything-configurable); an operator override records the source `operator`.

### 5.1 Worked example, three agents

```toml
# ddflow/harnesses/claude.toml (additions)
[headless]
resume = ["--resume", "{session}"]
model  = ["--model", "{model}"]

[tokens.cache]
control = "ttl-env"
ttl_values = ["5m", "1h"]
default_ttl = "5m"
set.main     = { env = "CLAUDE_CODE_PROMPT_CACHE_TTL", value = "{ttl}" }
set.subagent = { env = "CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL", value = "{ttl}" }   # needs 2.1.242+

[tokens.window]
set = { env = "CLAUDE_CODE_AUTO_COMPACT_WINDOW", value = "{tokens}" }              # 100000..1000000, beats flag/setting
unit = "tokens"
min = 100000
max = 1000000
threshold = { env = "CLAUDE_AUTOCOMPACT_PCT_OVERRIDE", value = "{percent}" }       # can only lower

[tokens.compact]
command = "/compact"
takes_instructions = true
instructions_region = "# Compact instructions"

[meters]
limit_signal = "hook"                      # StopFailure matcher rate_limit; also stream-json rate_limit_event
limit_match = "You've hit your (session|weekly|Opus|Sonnet) limit"
reset_source = "message"                   # 'limit resets 3:45pm'
query = "/usage"

[caps]
shell_output = { env = "BASH_MAX_OUTPUT_LENGTH", value = "{chars}" }
mcp_output   = { env = "MAX_MCP_OUTPUT_TOKENS", value = "{tokens}" }
```

```toml
# ddflow/harnesses/codex.toml (additions)
[headless]
resume = ["exec", "resume", "{session}"]        # codex exec resume [--last] [ID] [prompt]
model  = ["-m", "{model}"]

[tokens.cache]
control = "automatic"                           # prompt_cache_* is API-level; the CLI exposes none (U)

[tokens.window]
set = { argv = ["-c", "model_context_window={tokens}"] }
threshold = { argv = ["-c", "model_auto_compact_token_limit={tokens}"] }
unit = "tokens"

[tokens.compact]
command = "/compact"
prompt_setting = { argv = ["-c", "compact_prompt={text}"] }
# takes_instructions: absent (not verified)

[meters]
limit_signal = "message"
limit_match = "You've hit your usage limit"     # REPORTED; status = unverified
query = "/usage"

[caps]
tool_output = { argv = ["-c", "tool_output_token_limit={tokens}"] }
```

```toml
# ddflow/harnesses/kimi.toml (additions)
[headless]
resume = ["--session", "{session}"]
model  = ["-m", "{model}"]

[tokens.cache]
control = "none"                                # API supports prompt_cache_options; kimi-cli sets none (U)

[tokens.window]
set = { env = "KIMI_MODEL_MAX_CONTEXT_SIZE", value = "{tokens}" }
threshold = { file = "kimi.config.toml", key = "loop_control.compaction_trigger_ratio", value = "{ratio}" }   # --config-file in the lane dir
unit = "ratio"
min = 0.5
max = 0.99

[tokens.compact]
command = "/compact"
takes_instructions = true                       # '/compact preserve database-related discussions'

[meters]
limit_signal = "exit_code"
limit_exit_code = 75                            # print mode: retryable (429, 5xx, timeout); 1 = permanent
query = "/usage"

[caps]
max_completion = { env = "KIMI_MODEL_MAX_COMPLETION_TOKENS", value = "{tokens}" }
```

The `[verified]` block of each descriptor lists the URLs of section 8 and `unverified = [...]` for the U cells above.

## 6. Lowest-common-denominator behaviour for an unknown agent

When a descriptor has no `[tokens]`, `[meters]` limit keys or `[headless].resume`, ddflow assumes and says so (`not verified` in `harness show`, never `none`):

1. Cache: automatic, 5 minute TTL, no control. Keep tool list and instructions byte-stable; wait calls under 240 s (TE-poll-cadence); prefer fresh lane over waking after 5 minutes (context-compaction-cache.md section 4).
2. Compaction: the agent compacts at its own threshold, unreadable. ddflow advises rotation at the lane thresholds (150/250/350 K) by its own estimate (bytes/4 or transcript usage where B-am-ingest reads it) and never assumes `/compact` exists.
3. After compaction: no door. Put the charter and resume instructions in the instruction file the agent reads (AGENTS.md) and let the footer carry the rest on the next MCP call.
4. Output: no cap assumed; ddflow bounds its own outputs (`--brief` for agents, TE-bounded-outputs) and tells the agent to pipe shell output through a bound.
5. Limit: a stop is a stop; detect by the lane ending without a gate outcome plus the last message text matched against the descriptor's `limit_match` if any. Reset time unknown: D-TE-Q8 (operator: resume automatically) is served by a bounded probe ladder (resume attempts at 30 min, 1 h, 2 h, then hourly, capped by `[spend].auto_resume`), valid only where a rejected attempt costs no model tokens (ATF-Q7); otherwise wait for the operator.
6. Resume: use the session id if the client declared one at session start; otherwise start a fresh lane from the resume pack (CC-resume-pack).
7. Model: no cheap-model routing; the recorded model is whatever the agent declared at `session start`.

## 7. Open questions with defaults

| Q | Default |
|---|---|
| Add a `[tokens]` section (descriptor-v2 amendment) rather than put cache/window under `[caps]`/`[meters]`? | yes: those two are about limits and sizes, not knobs |
| May the launcher write a lane-private config file (Kimi ratio, Gemini/Qwen settings) for non-env knobs? | yes, inside the lane's own directory only, never user config |
| Probe real limit messages by exhausting a quota? | no; record from user-supplied transcripts only; the cell stays `unavailable` |
| Research Antigravity, Roo and closed IDEs further? | later, tier-2 agents; separate ATF follow-up if the operator uses them |

## 8. Sources (read 2026-10-23)

Claude Code: code.claude.com/docs/en/{env-vars,model-config,hooks,costs,cli-reference,sub-agents,interactive-mode,errors}. Codex: learn.chatgpt.com/docs/{config-file/config-reference,cli/reference,cli/slash-commands,hooks,non-interactive-mode,pricing};
developers.openai.com/api/docs/guides/prompt-caching. Copilot: docs.github.com/en/copilot/{reference/copilot-cli-reference/cli-command-reference,concepts/agents/copilot-cli/context-management,reference/hooks-configuration,reference/copilot-billing/models-and-pricing,concepts/usage-limits}; code.visualstudio.com/docs/copilot/customization/hooks.
Cursor: cursor.com/docs/{cli/reference/parameters,cli/reference/slash-commands,cli/reference/configuration,hooks,models-and-pricing,subagents,rules}. Gemini: github.com/google-gemini/gemini-cli docs/{cli/token-caching,cli/settings,reference/commands,hooks,core/subagents,resources/quota-and-pricing,cli/headless,cli/gemini-md}; ai.google.dev/gemini-api/docs/{caching,pricing}.
Kimi: github.com/MoonshotAI/kimi-cli docs/en/{configuration/config-files,configuration/env-vars,reference/slash-commands,reference/kimi-command,customization/{hooks,agents,mcp,print-mode}}; platform.kimi.ai/docs/guide/context-caching.md.
Qwen: github.com/QwenLM/qwen-code docs/users/{configuration/settings,features/{token-caching,hooks,sub-agents,headless},configuration/auth}. Goose: github.com/block/goose documentation/docs/guides/{environment-variables,sessions/smart-context-management,context-engineering/{hooks,subagents},goose-cli-commands,handling-llm-rate-limits-with-goose}.
Kilo: kilo.ai/docs/customize/context/context-condensing; github.com/Kilo-Org/kilocode packages/opencode/src/{provider/transform.ts,session/{compaction,overflow,retry}.ts,tool/truncate.ts}, packages/core flag.ts; local `kilo 7.8.12` `--help`, `run --help`, `stats --help`.
OpenCode: opencode.ai/docs/{config,cli,plugins,agents,rules,models}; github.com/anomalyco/opencode packages/ai/src/cache-policy.ts. Aider: aider.chat/docs/{usage/caching,config/options,usage/commands,usage/conventions,scripting}; aider source history.py, models.py, exceptions.py.
Cline: docs.cline.bot/{cli/cli-reference,features/subagents,sdk/guides/writing-plugins}. Roo: roocodeinc.github.io/Roo-Code/features/intelligent-context-condensing. Amp: ampcode.com/docs/markdown/{models-and-subagents,customize/plugins,customize/agents-md,cli/streaming-json,cli/execute-mode,pricing}.
Grok: docs.x.ai/build/{settings/reference,features/hooks,features/subagents,cli/reference,headless-scripting},docs.x.ai/developers/advanced-api-usage/prompt-caching. Windsurf: docs.windsurf.com/windsurf/cascade/{hooks,memories}.
Local: research/{dsh,dsh-probe,pi-dev,token-spend,context-compaction,context-compaction-cache}.md, R-usage-quota, R-kimi, R-model-routing.
