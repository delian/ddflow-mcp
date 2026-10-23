# Token-economy and compaction features on every supported coding agent

Task ATF-research (phase P-token-economy), agent res-agent-token-parity, 2026-10-23. Operator request (verbatim, 2026-10-23):
"can you make research for how to manage the same features on other coding agents, not only claude".
**Second pass** (task ATF-research-pass2, agent res-atf2, 2026-10-23), after the operator asked "What about antigravity, vscode copilot, qwen, and others for saving tokens - has this been investigated?":
evidence, URLs and quotes are in [agent-token-features-pass2.md](agent-token-features-pass2.md); every cell changed by it is listed in the changelog at the end of this file and tagged `(pass2 N.N)` in the cell.
Builds on [token-spend.md](token-spend.md), [context-compaction.md](context-compaction.md), [context-compaction-cache.md](context-compaction-cache.md)
(Claude facts and the break-even arithmetic are there and are not repeated). Proposals: [../decisions/agent-token-features.md](../decisions/agent-token-features.md).

Labels: **V** VERIFIED (vendor docs, source or local `--help` read on 2026-10-23), **R** REPORTED (blog, issue, forum, search
snippet), **U** UNKNOWN (looked, not found; where is in the notes). Nothing is guessed. Method caveat: docs were read through a
summarising fetch tool, pages over 100 K characters in parts, so a V cell is "a quoted setting name from the official page", not a
byte-level proof; every V that a launcher will depend on is re-checked by the recorded fixture of `ATF-fixtures`. **Pass 2 corrects this for Copilot, Antigravity, Qwen, Windsurf/Devin, Cursor, Roo, Cline, Continue, Zed, Junie, Kiro, Amp, Grok and Goose: those cells were read as raw markdown/HTML/source text with `curl`, no summarising proxy** (method in pass2 section 0); a V is still a quoted name, not an observed behaviour. The local
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
   Everywhere else the cache is automatic and fixed (Copilot states 1 h, or 24 h for OpenAI models, but the harness sets it): ddflow can only keep the prefix stable and avoid long idle gaps. Aider alone has keep-alive pings.
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
7. **Pass 2 (2026-10-23) found three levers that did not exist in the first pass** (evidence pass2 sections 1-4):
   (a) **a launch-time spend cap outside Claude**: Copilot `--max-ai-credits N` (soft, min 30), Qwen `--max-wall-time` / `--max-tool-calls` / `--max-session-turns`, Grok `--max-turns`, Junie `--max-steps`, Roo `allowedMaxCost`;
   (b) **per-run token and cache numbers in headless output**: Antigravity `agy -p --output-format json` carries `usage.cache_read_tokens`, Cline `run_result.usage`, Amp stream-json `cache_*`, Qwen `/stats`, Copilot SDK `assistant.usage` and `account.getQuota` (a real quota endpoint with `resetDate`);
   (c) **resident-prefix levers**: Qwen `tools.eager` + `tool_search` (byte-stable), Copilot/VS Code tool search (vendor-measured about 18 % fewer tokens on Anthropic models), Antigravity rules budget (24 KB per file, 20 000 tokens), Kiro `toolSearch.*`.
   Two corrections: Copilot's **premium-request multiplier is legacy** (since 2026-06-01 usage is per-token AI Credits, 1 credit = $0.01); and **Antigravity's FAQ forbids wrapping `agy` in another agent on consumer plans** (API key or Gemini Enterprise only).

## 2. The matrix

Rows are the agents of `ddflow/harnesses/*.toml` plus Pi (research/pi-dev.md); pass 2 added Antigravity rows to every table, and section 3 rows for Kiro, Junie, Continue and Zed (evidence in pass2 section 4; they have no descriptor yet); closed or cloud agents with no descriptor facts here
(cody, glm, openhands, qodo, replit, tabnine) get one line at the end. `windsurf` and `devin` are one family; `vscode` is Copilot in VS Code (section 2.x rows `Copilot`). Sources per agent are listed in section 8.

### 2.1 Cache control and price ratios (feature 1)

| Agent | TTL / breakpoints / explicit cache | What busts it | Hit / miss ratio (read vs base input) |
|---|---|---|---|
| Claude Code | **V** `CLAUDE_CODE_PROMPT_CACHE_TTL` (`5m`/`1h`, main, v2.1.242+, setting `promptCacheTtl`), `CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL` (non-main: subagents, workflows, background; setting `subagentPromptCacheTtl`), `ENABLE_PROMPT_CACHING_1H=1` (API-key/cloud users; subscriptions get 1h on main automatically), `FORCE_PROMPT_CACHING_5M=1` overrides all; `DISABLE_PROMPT_CACHING[_HAIKU\|_SONNET\|_OPUS\|_FABLE]`; `--exclude-dynamic-system-prompt-sections` moves per-user text out of the prefix; `__SYSTEM_PROMPT_DYNAMIC_BOUNDARY__` line splits a custom system prompt | tools/system/model/fast mode/images change, idle past TTL, compaction (context-compaction-cache.md section 3) | V 0.1x read (0.05x Sonnet/Opus 5.5), write 1.25x (5m) / 2x (1h); `/usage` shows `Prompt cache (main)` hit share and cause (v2.1.251+/260+) |
| Codex CLI | API level, automatic; `prompt_cache_key` optional; GPT-5.6+ `prompt_cache_options.ttl` `30m`/`12h`, earlier `prompt_cache_retention` `in_memory`/`24h`. **U** whether the CLI exposes or sets any of it (no key in the config reference) | model, tools order/schema, reasoning effort, verbosity, compaction, edit of earlier message, expiry | V 0.1x read (0.05x one model); write 1.25x/30m, 2.5x/12h (platform docs) |
| Copilot CLI / VS Code | **V** no user setting; the harness decides. Vendor text: caches expire after "24 hours for OpenAI models and 1 hour for most others"; VS Code sets `prompt_cache_retention: "24h"` on supported OpenAI models and spends up to four Anthropic `cache_control` breakpoints (tool defs end, system end, two rolling messages); VS Code Cache Explorer diffs consecutive requests (pass2 1.2) | model switch, reasoning effort, context size, enabled tools/MCP servers changed mid-session; return after the TTL | V per-model rates (`models-and-pricing.yml`): cached input 0.1x (GPT-5.3-Codex $1.75 vs $0.175), cache write 1.25x on Anthropic and GPT-5.6+, long-context tier about 2x input above 200K/272K; 1 AI credit = $0.01; Auto model 10 % discount on paid plans |
| Cursor | **U** no user control; price table lists cache write/read for Anthropic models (Sonnet 5.5 $2 input, $2.5 write, $0.1 read: 1.25x / 0.1x); Cursor's own models show no cache-write charge (pass2 4.2) | U | V as stated |
| Gemini CLI | none in CLI; implicit caching only for API-key and Vertex users, **not** OAuth/Code Assist; min 2048 (2.5) / 4096 tokens; explicit caches exist on the API, not used by the CLI | prefix change | V cached 0.1x (3.1 Pro $2.00 vs $0.20; Flash $0.75 vs $0.075); explicit storage $/M-hour |
| Kimi Code | API (k3): `prompt_cache_options` `{mode: implicit, ttl: 5m\|1h}`, default 5m; Messages API top-level `cache_control`; TTL locked at first write. **U** whether kimi-cli sets it (no cache env var) | prefix change, timestamps/random ids in prefix | V hit 0.1x ($0.30 vs $3.00), write $3.00/M (5m) or $6.00/M (1h), k3 only; K2.x U |
| Qwen Code | `model.generationConfig.enableCacheControl` (bool), "available for API key users", none for OAuth; `/stats` shows the cached share; `context.clearContextOnIdle.*` clears old tool results "so later turns keep reusing the provider prompt cache"; `tools.eager` + `tool_search` keep the declared tool list byte-stable. **TTL not settable** | tool-list/prefix change, rewriting history | **V** DashScope: explicit (`cache_control: ephemeral`) write 1.25x, hit 0.1x, 5 min reset on hit, min 1,024 tokens; implicit automatic, cannot be disabled, hit about 0.2x, validity indeterminate (pass2 3) |
| Goose | **V** `GOOSE_CACHE_TTL` `5m`/`1h` (Anthropic only; 1h writes 2x; **headless `goose run`, subagents and scheduled recipes always 5m**); caching auto-enabled for Claude via Anthropic/Bedrock/Databricks/OpenRouter | not documented | read ratio is Anthropic's (0.1x), not stated by Goose |
| Kilo | **V** automatic: `cacheControl ephemeral` on first 2 system + last 2 non-system messages (Anthropic/OpenRouter/Alibaba), `cachePoint` (Bedrock); provider option `setCacheKey`; **no TTL field sent** (5m); 1h not settable (U) | prune pass ("[Old tool result content cleared]") and compaction rewrite the prefix | Anthropic ratios (not fetched here) |
| OpenCode | **V** (dev branch source) `cache-policy.ts` default `auto` (last tool def, first+last system, tail; max 4) or `none`; `CacheHint.ttlSeconds` exists at library level; user setting for it **U**; `provider.<id>.options.setCacheKey` | U | Anthropic ratios |
| Aider | **V** `--cache-prompts` / `AIDER_CACHE_PROMPTS` (off by default; Sonnet/Haiku, DeepSeek); TTL fixed 5m, **`--cache-keepalive-pings N`** pings every 5 min; `--no-stream` to see stats | history order, changed read-only/editable files, repo map | Anthropic / DeepSeek ratios |
| Cline | **U** for any user setting; SDK source applies `createEphemeralCacheControl()` to the last text part (Anthropic-compatible providers) and a Bedrock `cachePoint`; docs: agentic compaction "leverages your existing prompt cache" (pass2 4.4) | compaction, history rewrite | per provider; ClinePass reference table lists cached-read prices (e.g. DeepSeek V4.1 Flash $0.006/M) |
| Roo | **V** (source) the Anthropic provider hard-codes `cache_control: {type: "ephemeral"}` on the system prompt and the last two user messages; no TTL setting; toggles `awsUsePromptCache`, `litellmUsePromptCache`; multi-point strategy with `minTokensPerCachePoint` / `maxCachePoints` (pass2 4.3) | condensing rewrites history | provider's |
| Amp | server-side, no knob (**U**); `usage` in stream-json shows `cache_creation_input_tokens`/`cache_read_input_tokens`; **V** switching mode mid-thread "invalidates the prompt cache" | mode switch | provider list prices, no markup for individuals (V pricing page) |
| Grok CLI | API automatic prefix cache; `x-grok-conv-id` (Chat Completions) or `prompt_cache_key` (Responses) routes to the same server, "not 100 % guaranteed"; no TTL knob (**U**); whether the CLI sets the header **U** | changed earlier messages; dropping `reasoning_content` | **V** grok-4.7 cached $0.50 vs input $2.00 (0.25x) below 200K prompt tokens, $1.00 vs $4.00 above; grok-4.5 cached $0.30 |
| Windsurf (Cascade) / Devin CLI | **U** for any control; quota page: "requests to the same model leverage caching"; usage shows "Input (cache write)" / "(cache read)"; SWE-2 $0.30/M cache input vs $3 input (pass2 4.1) | switching model | V SWE-2 0.1x |
| Antigravity (`agy`) | **U** no cache setting on any page read; `usage.cache_read_tokens` is reported in `-p` json and stream-json (pass2 2); model-side caching follows Gemini rules (context-compaction-cache.md) | U | not published on the Antigravity pages; plan quota counts work, not tokens |
| dsh | **U** for the harness (provider decides; DeepSeek disk cache automatic, hit $0.003 vs miss $0.15 per M = 2% for flash, V context-compaction-cache.md) | prefix change | per backing model |
| Pi | "cache-preserving by design": changes append a delta message instead of rewriting the system prompt (V pi-dev.md); TTL control **U** | U | per provider |

### 2.2 Auto-compact and window knobs (feature 2)

| Agent | Threshold / window setting | Manual command (custom instructions?) | Larger window / extend |
|---|---|---|---|
| Claude Code | **V** `CLAUDE_CODE_AUTO_COMPACT_WINDOW` (100000-1000000 tokens; `200k` suffix v2.1.304+; beats `/autocompact`, `--autocompact`, setting `autoCompactWindow`), `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` (1-100 % of the window, can only lower), `DISABLE_AUTO_COMPACT=1` (also idle compaction), `DISABLE_COMPACT=1`; subagent frontmatter `autoCompactWindow` (v2.1.296+); default without setting: model limit, ~967K on 1M models | **V** `/compact <text>`; `/autocompact 500k`; `--autocompact <auto\|tokens>` per launch (v2.1.221+) | `[1m]` model suffix (`opus[1m]`, `sonnet[1m]`); `CLAUDE_CODE_DISABLE_1M_CONTEXT=1`; at runtime `/model`, `/autocompact` |
| Codex | **V** `model_auto_compact_token_limit`, `model_auto_compact_token_limit_scope` (`total`/`body_after_prefix`), `model_context_window`; via config.toml or `-c key=value` | `/compact` (instructions **U**); `/status` shows remaining context | model choice only (`-m`); no long-context flag (U) |
| Copilot CLI | **V** auto at ~80 % of the window in the background, pause near 95 %; no threshold setting in the settings table (V by absence). `--context default\|long_context` / setting `contextTier` choose the window tier (1M extended; input price about 2x above 200K/272K). VS Code: `github.copilot.chat.summarizeAgentConversationHistory.enabled` (Experimental, default `true`), `chat.agent.maxRequests` (25) | **V** `/compact [FOCUS-INSTRUCTIONS]` (Esc cancels; irreversible), VS Code `/compact <text>` and "Compact Conversation"; `/context`, `/session checkpoints` | `--context long_context`; model dependent |
| Cursor | threshold **U**; `maxMode` bool in `~/.cursor/cli-config.json` and `/max-mode` (legacy request-based plans only, +20 %); extended 1M context on Claude/GPT-5.6 models (V); model parameter brackets such as `claude-opus-5[context=300k]` shown for subagent `model:`, CLI use **U**; `preCompact` input carries `context_usage_percent`, `context_tokens`, `context_window_size` (pass2 4.2) | `/summarize` (alias `/compress`); instructions **U** | Max Mode / extended context / `[context=N]`; price 2x above 256K (Grok 4.7) or 5x above 100K (Haiku 5.5) |
| Gemini CLI | **V** `model.compressionThreshold` (default 0.5 of context), `experimental.contextManagement`; no window override (**U**) | `/compress` (no argument documented) | model property (Pro >200K tier priced higher) |
| Kimi | **V** `[loop_control]` `compaction_trigger_ratio` (0.85, 0.5-0.99), `reserved_context_size` (50000), whichever first; window per model `max_context_size`, env `KIMI_MODEL_MAX_CONTEXT_SIZE` | **V** `/compact [instructions]`, `/clear` | `[models.X] max_context_size`; long-context switch U |
| Qwen | **V** `context.autoCompactThreshold` (>0 to 1, default 0.85, "a ceiling on the trigger"; replaces removed `model.chatCompression.contextPercentageThreshold`), `compactionModel` (`/model --compaction`), `model.sessionTokenLimit`, `context.clearContextOnIdle.{toolResultsThresholdMinutes 60, toolResultsNumToKeep 5, toolResultsTotalCharsThreshold 500000}`, `model.chatCompression.maxRecentFilesToRetain` / `QWEN_COMPACT_MAX_RECENT_FILES`, `maxRecentImagesToRetain` / `QWEN_COMPACT_MAX_RECENT_IMAGES`; window `model.generationConfig.contextWindowSize` | `/compress` (alias `/summarize`); instructions U (hook input has `custom_instructions`) | settings file only; no env for the window |
| Goose | **V** `GOOSE_AUTO_COMPACT_THRESHOLD` (0-1, default 0.8, 0 disables), `GOOSE_AUTO_COMPACT_TOKEN_LIMIT` (default 225000); trigger = min of the two; `GOOSE_CONTEXT_LIMIT` (window override, default 128000), `GOOSE_TOOL_CALL_CUTOFF` | `/compact` (no argument documented) | `GOOSE_CONTEXT_LIMIT` |
| Kilo | **V** `compaction.auto` (true), `compaction.threshold_percent`, `compaction.prune` (true, 40K recency), `compaction.tail_turns` (2), `compaction.preserve_recent_tokens`, `compaction.reserved`; env `KILO_DISABLE_AUTOCOMPACT`, `KILO_DISABLE_PRUNE`; `agent.compaction.model` | `/compact` (aliases smol, condense); instructions none documented | `provider.<id>.models.<m>.limit.context`; models with no declared window are never auto-compacted |
| OpenCode | **V** `compaction.auto`, `compaction.prune` (docs default false), `compaction.reserved`; dev schema `compaction.buffer`, `compaction.keep.tokens`; env `OPENCODE_DISABLE_AUTOCOMPACT`, `OPENCODE_DISABLE_PRUNE` | `/compact` (alias `/summarize`, R); instructions U | `limit.context/limit.output` per custom model |
| Aider | **V** no whole-context compaction; history summarised by the weak model above `--max-chat-history-tokens` (default min(max(max_input/16,1024),8192)) | `/clear`, `/reset`, `/tokens` (no `/compact`) | `--model-metadata-file` |
| Cline | **V** `--compaction agentic\|basic\|off` (default agentic); SDK constants `COMPACTION_TRIGGER_RATIO = 0.9`, `DEFAULT_TARGET_RATIO = 0.7`, `DEFAULT_PRESERVE_RECENT_TOKENS = 20_000` (source; override **U**) | `/smol` (alias `/compact`), `/newtask`; instructions U | U |
| Roo | **V** settings `autoCondenseContext` (default true) and `autoCondenseContextPercent` (default 100; `MIN_CONDENSE_THRESHOLD` 5, max 100); a context-window error forces a cut to 75 % (`FORCED_CONTEXT_REDUCTION_PERCENT`); default reserve 30 % (20 % output + 10 % buffer); condensing uses the active conversation model (`condensingApiConfigId` is no longer in the schema) | "Condense Context" button (no instructions); custom prompt (section 2.3) | U |
| Amp | automatic by a supporting model, main thread and subagents alike; **V** "the configured percentage threshold, which defaults to 90 %" (setting name **U**); plugin agents can set `compactionThresholdTokens`; alternative "handoff" to a new thread | none found (U) | n/a |
| Grok CLI | **V** `[session] auto_compact_threshold_percent` (0-100, default 85), `[model.<id>] context_window` (BYOK, "drives auto-compact timing") | **V** `/compact [context]` (optional instructions to preserve), `/context` | BYOK window |
| Windsurf (Cascade) / Devin CLI | Cascade **U**; Devin CLI threshold **U**; Cascade `devin.autoContinue` (default 40 invocations per prompt) is a loop cap, not a context knob | Devin CLI **V** `/compact` ("Force conversation compaction", no instructions on the page), `/context` | U |
| Antigravity (`agy`) | automatic checkpoint compaction; **no threshold or window setting** in the CLI settings table (V by absence); compaction budgets are sized "from the model's full context window" (changelog) | **V** `/compact [message]` (CLI v1.3.5 2026-10-14, app v2.24.0), `/context`, `/clear`, `/rewind`, `/fork`, `/btw` | model choice only |
| dsh | **V** `compaction-basic`, `/compact`, tool-result pruner (apps/cli/composition.md); per-model context window in `llm-pi-ai` config; threshold setting **U** | `/compact` | per-model `input`/window in profile patch |
| Pi | **V** automatic near the limit, summary entry added, originals kept; `compact` RPC command | `/compact` / RPC `compact` | provider/model choice |

### 2.3 Compaction hooks, summary replacement, instructions that survive (feature 3)

| Agent | Hooks around compaction | Custom prompt / replace summary | Instruction file and survival |
|---|---|---|---|
| Claude Code | **V** `PreCompact` (matcher `manual`/`auto`; may block via exit 2 or `decision`), `PostCompact` (no control), `SessionStart` source `compact` (injects `additionalContext`); resume payload has `seconds_since_last_response`, `context_tokens`, `prompt_cache_likely_expired`, `estimated_cache_write_usd` | no replacement; `/compact <text>`; hook context is itself summarised away (context-compaction.md) | `CLAUDE.md` (+ AGENTS.md conditional), **`# Compact instructions` section is honoured (V costs page)**; that CLAUDE.md is re-read after compaction is the documented design (system layer stays), not separately probed (U) |
| Codex | **V** `PreCompact`/`PostCompact` (matcher `trigger`, stdout ignored), `SessionStart` matcher `source: "compact"` (stdout / `additionalContext` becomes developer context) | **V** `compact_prompt`, `experimental_compact_prompt_file`; replacement U; server compaction is opaque | `AGENTS.override.md` > `AGENTS.md` > `project_doc_fallback_filenames`, root to cwd, `project_doc_max_bytes` 32 KiB (R); `developer_instructions`, `model_instructions_file` (V); reload after compaction U; no "Compact instructions" analogue (U) |
| Copilot | **V** CLI `preCompact` (alias `PreCompact`; input `trigger`, `customInstructions`, `transcriptPath`): "No - notification only"; VS Code `PreCompact` input `{trigger: "auto"}`; **no PostCompact and no SessionStart(compact) in either reference**; `sessionStart`, `postToolUse` (cap 10K) and `subagentStart` inject `additionalContext` (pass2 1.3) | none (focus text on `/compact` only) | `.github/copilot-instructions.md`, `AGENTS.md`, `CLAUDE.md`, `.github/instructions/**/*.instructions.md` (`applyTo`), `~/.copilot/copilot-instructions.md` (V); part of the system prompt (`/context` slice); compaction keeps "original user instructions" |
| Cursor | **V** `preCompact` ("cannot block or modify"; input `trigger`, `context_usage_percent`, `context_tokens`, `context_window_size`, `messages_to_compact`); no post (U) | none (U) | `.cursor/rules/*.mdc` (`description`, `globs`, `alwaysApply`; plain `.md` ignored), `AGENTS.md`, User Rules; survival through summary U |
| Gemini CLI | **V** `PreCompress` (trigger auto/manual; `systemMessage` only, "cannot block or modify"); no post | none found (U) | `GEMINI.md` (global, workspace, parents, JIT), `context.fileName` list e.g. `["AGENTS.md","GEMINI.md"]`; sent with each prompt (so survives, R by design) |
| Kimi | **V** `PreCompact`, `PostCompact` exist (`[[hooks]]` in config.toml, Beta); capabilities U | none found (U) | `AGENTS.md` root to cwd + `.kimi/AGENTS.md`, in the system prompt each turn (inference: survives); subagents U |
| Qwen | **V** `PreCompact` (input `trigger`, `custom_instructions`; output `hookSpecificOutput.additionalContext`), `PostCompact` (input `compact_summary`; "output is ignored"), `SessionStart` source `compact`, `InstructionsLoaded` | custom prompt U; no replacement | `QWEN.md`, `context.fileName` list (AGENTS.md only if listed, U default); `paths:`-gated rules and skills cut resident cost |
| Goose | **none** (no PreCompact/PostCompact in the hooks event list) | **V** edit `compaction.md` template (prompt templates) | `AGENTS.md`, `.goosehints`, `CONTEXT_FILE_NAMES`; **`GOOSE_MOIM_MESSAGE_TEXT` / `_FILE` (<= 64 KB) injected every turn, survives compaction** |
| Kilo | plugin hook **V** `experimental.session.compacting` (`output.context[]` append, `output.prompt` replace), `experimental.compaction.autocontinue`; no post hook | **V** replace prompt via plugin | `AGENTS.md`, `instructions` array in kilo.json; re-sent each turn (survival inferred; U stated) |
| OpenCode | **V** same plugin hook, event `session.compacted` | **V** replace via plugin | `AGENTS.md` (CLAUDE.md fallback), `~/.config/opencode/AGENTS.md`, `instructions`; survival U |
| Aider | none | none; `--read CONVENTIONS.md` is read-only, cached, never summarised | `--read`, `.aider.conf.yml` `read:`; AGENTS.md auto-load U |
| Cline | source examples (`sdk/examples/plugins/custom-compaction.ts` `registerMessageBuilder`, `hooks/custom-compaction-hook.example.ts` `beforeModel`) **replace** compaction; documented hook set `beforeRun/afterRun/beforeTool/beforeModel`; `--hooks-dir` | no documented summary prompt (U); replacement via plugin (source V) | `.clinerules/`, `.cline/rules/`, `.cursorrules`, `.windsurfrules`, `AGENTS.md`, `~/.cline/rules`, `paths:` frontmatter (V) |
| Roo | none documented | **V** Settings > Context Management > "Custom Context Condensing Prompt" (`customSupportPrompts.CONDENSE`; legacy `customCondensingPrompt` migrated); slash commands in the first message survive | `.roo/rules/`, `.roo/rules-{mode}/`, `.roorules`, global `~/.roo/rules/` (V); AGENTS.md U |
| Amp | plugin API events `session.start`, `tool.call`, `tool.result`, `agent.start`, `agent.end`; no compaction event found (U) | none | `AGENTS.md` cwd/parents/`~/.config/amp/AGENTS.md`/`/etc/ampcode/AGENTS.md` (V); survival U |
| Grok CLI | **V** `PreCompact`, `PostCompact` events (influence on summary U); only `PreToolUse` blocks, "for passive events, stdout is ignored" | U | AGENTS.md, CLAUDE.md, `.grok/rules/*.md` ("no size cap"; `grok inspect` shows token counts), `--rules <TEXT>` |
| Windsurf (Cascade) / Devin CLI | Cascade: `pre_user_prompt`, `post_cascade_response` (block only); Devin CLI **V** `PostCompaction` (after success), `SessionStart` and `UserPromptSubmit` (`hookSpecificOutput.additionalContext` injected); whether `PostCompaction` can inject **U**; `.devin/hooks.v1.json` | none | `.devin/rules/*.md` (fallback `.windsurf/rules`; 12,000 chars per file, global 6,000), `AGENTS.md`; Devin CLI also `AGENTS.local.md`, `CLAUDE.md`, `.windsurfrules`, `~/.config/devin/AGENTS.md` (V) |
| Antigravity | **V** `hooks.json` events `SessionStart`, `PreToolUse`, `PostToolUse`, `PreInvocation`, `PostInvocation`, `Stop`; **no compaction event**; `injectSteps` (`systemMessage` / `ephemeralMessage`) from SessionStart, PreInvocation, PostInvocation | none | `AGENTS.md` / `GEMINI.md` / `.agents/rules/*.md` (`always_on` injected every turn); 24,000 bytes per file and a 20,000-token aggregate rules budget, over-budget rules demoted to path pointers (V) |
| dsh | **none** (bridges have no pre-compaction event, V dsh.md) | none | `AGENTS.md`/`CLAUDE.md` per directory, 65 536-byte budget |
| Pi | **V** extension event `session_before_compact` (may supply the summary), `agent_before_settle` | **V** extension supplies the summary; originals kept | `AGENTS.md`, `AGENTS.override.md`, `CLAUDE.md`; `--no-context-files` |

### 2.4 Output caps and subagents (features 4 and 5)

| Agent | Bound tool/shell/MCP output; max tokens; brief mode | Subagent: isolated context, summary back, model, per-lane env |
|---|---|---|
| Claude Code | **V** `BASH_MAX_OUTPUT_LENGTH` (30000 chars, max 150000; setting `bashOutputMaxChars`), `MAX_MCP_OUTPUT_TOKENS` (25000; warn at 10 000; >50 000 chars saved to a file; per-tool `anthropic/maxResultSizeChars`), `CLAUDE_CODE_MAX_OUTPUT_TOKENS`, `MAX_THINKING_TOKENS`, `/effort`; PostToolUse `updatedToolOutput`, PreToolUse `updatedInput` (rewrite the command) | **V** own context, "returns only the summary"; model: Agent `model` param > frontmatter `model` > `CLAUDE_CODE_SUBAGENT_MODEL` (`..._FORCE`) > parent; frontmatter `autoCompactWindow`, `effort`, `maxTurns`, `hooks`, `mcpServers`; **no `env` frontmatter**, so per-lane env is the launch environment of the whole process |
| Codex | **V** `tool_output_token_limit`, per-MCP-tool `mcp_servers.<id>.tools.<tool>.output_token_limit`, `tool_timeout_sec`, hook `additionalContextLimit` (2500 tokens), `model_verbosity=low`; max output tokens U | **V** config `agents.enabled`, `agents.default_subagent_model`, `agents.default_subagent_reasoning_effort`, `agents.<name>.config_file`, `agents.max_concurrent_threads_per_session`; isolation vs fork **U/R conflicting**; per-lane: `CODEX_HOME`, `-p profile`, `-c` |
| Copilot | **V** `COPILOT_LARGE_OUTPUT_THRESHOLD_BYTES` (20480; larger goes to a temp file with a preview; MCP tools too), `--max-ai-credits N` / `/limits set max-ai-credits` (soft cap, min 30), `--effort`, `-s/--silent`; VS Code `chat.tools.compressOutput.enabled` (Preview, false), `chat.agent.maxRequests` (25) | **V** own session, no inherited history; settings `subagents.agents.<name>` `{model, modelPolicy, effortLevel, contextTier}`, `subagents.maxConcurrency/maxDepth`, env `COPILOT_SUBAGENT_MAX_CONCURRENT/DEPTH`; VS Code `chat.subagents.defaultToAuto`; frontmatter `model`; `--fleet`; per-lane `COPILOT_HOME`, `COPILOT_MODEL` |
| Cursor | none documented (U) | **V** own context, "the parent only sees the final summary"; "five subagents ... roughly five times the tokens"; frontmatter `model` (`inherit`, bracket parameters), `readonly`, `is_background`; reads `.cursor/agents`, `.claude/agents`, `.codex/agents`; per-lane `CURSOR_CONFIG_DIR` |
| Gemini CLI | **V** `tools.truncateToolOutputThreshold` (40000 chars), `model.maxSessionTurns`; max output tokens U; MCP limit U | **V** independent context, findings back; `.gemini/agents/*.md` frontmatter `model`, `max_turns` 30, `timeout_mins` 10; `agents.overrides.<name>.modelConfig.model` |
| Kimi | **V** `KIMI_MODEL_MAX_COMPLETION_TOKENS`, `--max-steps-per-turn`, `--final-message-only`; tool-output truncation U | **V** `Agent` tool (`coder`/`explore`/`plan`), own context under `subagents/<id>/`, optional `model` per launch; summary step not documented; per-subagent env U |
| Qwen | **V** `tools.truncateToolOutputThreshold`, `tools.truncateToolOutputLines` (defaults U), `QWEN_CODE_MAX_OUTPUT_TOKENS`, `--max-session-turns` (exit 53), `--max-wall-time`, `--max-tool-calls` (budget exit 55), `--output-style Concise`, `tools.eager` (defer tool schemas), `mcp.toolIdleTimeoutMs`; MCP size limit U | **V** separate context; frontmatter `model: inherit\|fast\|<id>`; `agents.builtin.exploreModel`, `model.maxSubagentDepth`; subagents with no tool list receive every schema; fork subagents inherit parent context |
| Goose | **V** `GOOSE_MAX_TOKENS`, `GOOSE_MAX_TOOL_RESPONSE_SIZE` (200000 chars; larger goes to a temp file), `GOOSE_TOOL_CALL_CUTOFF`, `GOOSE_MAX_TURNS`, `GOOSE_SUBAGENT_MAX_TURNS` | **V** isolated instances returning `execution_summary`; `GOOSE_SUBAGENT_PROVIDER`, `GOOSE_SUBAGENT_MODEL`, `GOOSE_MAX_CONCURRENT_SUBAGENTS` (5); cache TTL forced 5m in subagents |
| Kilo | **V** `tool_output.max_lines` (2000) / `max_bytes` (51200, read in source, schema U), overflow saved to a file for 7 days; `KILO_EXPERIMENTAL_OUTPUT_TOKEN_MAX`; MCP limit U | child sessions via Task tool; `agent.<name>` `mode: subagent`, `model`, `steps`; summary return R; per-lane `KILO_CONFIG_CONTENT`, `KILO_CONFIG`, `KILO_CONFIG_DIR` |
| OpenCode | `tool_output.*` (same lineage, not on the config doc), `OPENCODE_EXPERIMENTAL_OUTPUT_TOKEN_MAX` | General/Explore/Scout subagents, per-agent `model`, `subagent_depth` 1, per-lane `OPENCODE_CONFIG`, `OPENCODE_CONFIG_DIR`, `OPENCODE_CONFIG_CONTENT` |
| Aider | `--map-tokens`, `--timeout`; no truncation, no MCP | none; `--weak-model` (summaries, commits), `--editor-model` |
| Cline | `-t/--timeout`, `--retries`; caps U | **V** `spawn_agent` (CLI/Desktop): separate context, final reply returned, usage added to the session total, disable the tool in `/settings`; no MCP tools inside; model U; per-lane `CLINE_DIR`, `--config`, `--data-dir`, `--hooks-dir` |
| Roo | **V** `terminalOutputPreviewSize` small/medium/large = 5/10/20 KB (rest to disk, `read_command_output`), `maxImageFileSize`, `maxTotalImageSize`, `maxOpenTabsContext`, `maxWorkspaceFiles`, `maxDiagnosticMessages`; `allowedMaxRequests` / `allowedMaxCost` cap auto-approval | **V** Orchestrator `new_task`: isolated subtask, only the `attempt_completion` summary returns; per-mode model via API profiles (not a launch flag, U) |
| Amp | U (`amp.tools.disable` drops tools) | **V** Task subagent and Read Thread: own context, main agent receives the final summary; same model and effort as the thread; plugin agents take `model` |
| Grok CLI | **V** `[toolset.bash] output_byte_limit` (20000), `--max-turns` | **V** "independent child sessions ... return a summary to the parent"; `.grok/agents`; `GROK_SUBAGENTS=0` / `--no-subagents`; `[subagents.models]` maps subagent to model id; per-lane `GROK_HOME`, `GROK_DEFAULT_MODEL` |
| Windsurf (Cascade) / Devin CLI | Cascade: 40 invocations per prompt (`devin.autoContinue`); rest U | Devin CLI **V** own context per subagent, costs separately; `subagent_explore` model chosen by a server router, `subagent_general` inherits; config `agent.subagent_model`; custom subagent `model` field; Cascade has no subagents |
| Antigravity | **V** MCP limits: 5 min to connect, 3 min per tool call (a positive value in seconds replaces both, negative turns them off); `verbosity: low` collapses tool activity into a token usage summary; rules 24 KB per file; other caps U | **V** custom subagents (`.md` + frontmatter): `model` is `inherit`, `flash` or `pro` (a tier), clean-slate context ("does not inherit the parent's existing conversation history"), workspace `inherit`/`branch`/`share`, nesting 10, stream `subagent_info`; `--agent` |
| dsh | stdout/event lines capped by the `--json` protocol (32 KiB lines); tool limits U | native tools, definition files not found; MCP stdio env scrubbed of `DSH_*` and `*KEY*`/`*TOKEN*` names |
| Pi | U | extensions; no built-in subagents (pi-dev.md); `tool_search`/codemode tiers cut tool schema size |

### 2.5 Spend and limit awareness, resume (feature 6)

| Agent | Limit signal and reset | Query remaining quota | Resume (headless where noted) |
|---|---|---|---|
| Claude Code | **V** messages `You've hit your session limit` / `weekly limit` / `Opus limit` / `Sonnet limit` (reset time in the text, e.g. `limit resets 3:45pm`), `monthly spend limit`, `Request rejected (429)`; hook **`StopFailure`** matcher `rate_limit` (also `billing_error`, `overloaded`, `max_output_tokens`); stream-json `rate_limit_event` with utilization + `resets_at` for 5-hour and 7-day windows (R-usage-quota, binary probe 2.1.283); auto-wait and continue after reset (`autoContinueAtUsageLimit`, v2.1.234+), `CLAUDE_CODE_RETRY_WATCHDOG=1` for unattended retry; exit code on limit **U** | **V** `/usage` (bars, `Showing last-known usage`), statusline `rate_limits`, `~/.claude.json` `cachedUsageUtilization` (stale, R-usage-quota), OTel, Admin APIs | **V** `--resume <id\|name>`, `-c`, `--session-id`, `--fork-session`, `-p --continue`; `--max-budget-usd`, `--max-turns` |
| Codex | `You've hit your usage limit` (R); 5-hour and weekly windows; exit code U | **V** `/usage [daily\|weekly\|cumulative]`, `/status`, `/statusline` rate limits; R rollout files `~/.codex/sessions` `token_count.rate_limits`, headers `x-codex-primary-used-percent`, `GET /api/codex/usage` | **V** `codex resume [ID] [--last]`, `codex exec resume [--last] [ID] [prompt]`; `--ephemeral` disables rollout files |
| Copilot | AI Credits allowance resets 00:00:00 UTC on the 1st (V); `--max-ai-credits` / `/limits` soft cap stops the run cleanly (exit code and message **U**; scope per response vs per session: the docs conflict); setting `continueOnAutoMode` switches to auto on a rate limit; rate-limit text U | **V** SDK RPC `account.getQuota` (`quotaSnapshots.premium_interactions\|chat\|completions`, `resetDate`), `session.usage.getMetrics` (credits = `totalNanoAiu`/1e9), event `assistant.usage`; CLI `/usage` (per-model tokens and credits), `footer.showQuota`; VS Code status dashboard; no CLI flag/API for account quota | **V** `--continue`, `-r/--resume[=VALUE]` (ID/prefix/name; explicit ID under `-p`), `--session-id`, `COPILOT_ENABLE_INTERRUPTED_SESSION_RESTORE=1` |
| Cursor | weekly pool shared by Agent/CLI/Cloud; reset date in settings; "requests are never downgraded" (V); prepaid credit, Auto Top-up and a monthly additional-usage limit; text, exit code U | editor settings only; Admin API (usage events, spend, per-user limit) for Teams/Enterprise; individual CLI command/API U | **V** `--resume [chatId]`, `--continue`, `agent ls`, `/fork` |
| Gemini CLI | daily request caps by tier (1000 / 1500 / 2000 / 250), reset time U; 429 text U; fallback-to-Flash on 429 is string-matched (R #13112); **exit codes 0, 1, 42 input, 53 turn limit** (not limit) | **V** `/stats model` | **V** `--resume/-r latest\|index`, `/resume`, `/chat` checkpoints |
| Kimi | **V print-mode exit 75 = retryable (429, 5xx, timeout), 1 = permanent**; quota refreshes every 7 days plus a rolling 5-hour window (R, wording ambiguous); text U | **V** `/usage` (alias `/status`, Kimi Code platform only) | **V** `--continue/-C`, `--session/-S <id>`, `--resume/-r`, `/fork`; print + resume U |
| Qwen | `QWEN_CODE_UNATTENDED_RETRY=1` (V, strict `true`/`1`) keeps a headless run alive through 429/529 (backoff cap 5 min, stderr heartbeat 30 s); text U; exit 53 turn limit, 55 wall/tool budget | `/stats` (alias `/usage`) token stats with cached share; hook input `context_usage`; plan quota via provider console | **V** `--continue`, `--resume [id]`, `/resume`; works with `-p` |
| Goose | none built in (V guide recommends routers that auto-switch); text U | `GOOSE_CLI_SHOW_COST` estimates only | **V** `goose session --resume [-n name\|--session-id id]`, `goose run --resume`, `--fork` |
| Kilo | regex on `rate limit\|too many requests\|try again later` + `retry-after(-ms)` headers, capped backoff, `KILO_SESSION_RETRY_LIMIT`; **`kilo run` exit 0 / 124 timeout / 1 error** | **V** `kilo stats`, `kilo session list`, `kilo export`, local DB `KILO_DB` | **V** `-c`, `-s <id>`, `--fork` |
| OpenCode | same lineage (retry on text and headers); exit codes U | **V** `opencode stats`, `export` | **V** `run -c/-s/--fork` |
| Aider | litellm `RateLimitError` message "The API provider has rate limited you. Try again later or check your quotas."; retry 0.125 s doubling, gives up after 60 s; exit code U | none; cost shown per message | `--restore-chat-history`, `--chat-history-file` (not a session resume) |
| Cline | error as `{"type":"error",...}` on stderr; exit codes "not documented" (V); ClinePass has 5-hour, weekly and monthly limits (V) | `run_result.usage` and `usage` events in `--json`; dashboard app.cline.bot; no quota command | **V** `--id <session-id>`, `cline history --json` |
| Roo | `allowedMaxRequests` / `allowedMaxCost` stop auto-approval (V); provider limits U | cost estimate per request in the UI; `stream-json` output; no quota query | **V** `roo --print`: `--session-id`, `-c/--continue`, `--create-with-session-id`, `--stdin-prompt-stream`, `--ephemeral`, `--oneshot` |
| Amp | credits, monthly tier resets at period end (V); purchased credits expire after 12 months; text, exit code U | **V** `amp usage` (pricing page; output format not shown) | **V** `amp threads continue T-... [--execute --stream-json]` |
| Grok CLI | weekly pool, next reset date in the usage view (V); text U | **V** `/usage` TUI command ("View credit usage or manage billing"); headless form U | **V** `-r/--resume [ID]`, `-c`, `-s <uuid>`, `--fork-session`, `grok sessions list` |
| Windsurf (Cascade) / Devin CLI | quota-based since March 2026: daily and weekly allowance, "free models don't count against your quota" (V); reset shown in the app | Devin CLI `/usage` (session credits/ACU), `/session-stats`; team API `POST https://server.codeium.com/api/v1/GetUsageConfig` (service key, admin; response U) | **V** (Devin CLI) `devin -p`, `-c/--continue`, `-r/--resume <id>`, `--model` / `DEVIN_MODEL`, `--export` |
| Antigravity | Ultra/Pro quota "refreshed every five hours until the weekly limit is reached"; Free weekly; at exhaustion the CLI says "Your AI credits balance is too low to continue." (changelog); `useG1Credits` overage setting; exit code U; transient errors retried with backoff about 12 min | **V** `/usage` (alias `/quota`) per-model quota panel, `/credits` balance, statusline `AI Credits: N`; `agy -p /usage` returns text (format U); `usage` object in every json/stream-json run | **V** `-c/--continue`, `--conversation ID`, `/resume`, `/fork`; prints the resume command at exit |
| dsh | exit 0 done, 1 aborted/errored (V); a `status` event exists in `--json`; limit text U | U | **V** resume by id; ACP `session/resume` |
| Pi | U | U | RPC `get_state`, `fork`; sessions JSONL (V pi-dev.md); resume flag U |

### 2.6 Injection channels and routing (features 7 and 8)

| Agent | Hook door that reaches the model (V unless noted) | Cheaper model per run/lane; headless line; MCP at launch |
|---|---|---|
| Claude Code | `SessionStart`, `UserPromptSubmit`, `PostToolUse`, `SubagentStart`, `SubagentStop` accept `additionalContext`; `PreCompact` and `PostCompact` do not; files `CLAUDE.md`, `.claude/rules/*.md`, `.claude/settings.json` | `--model`, `ANTHROPIC_MODEL`, `ANTHROPIC_DEFAULT_{OPUS,SONNET,HAIKU,FABLE}_MODEL`, `CLAUDE_CODE_SUBAGENT_MODEL`, frontmatter `model`, `--effort`, `--fallback-model`; `claude -p --output-format stream-json --settings <file\|json> --mcp-config <f> --strict-mcp-config --bare` |
| Codex | `SessionStart`, `UserPromptSubmit` stdout/additionalContext, `PostToolUse` additionalContext (plain stdout ignored); `[hooks]` in `config.toml`/`hooks.json`, `features.hooks` | `-m`, `-p profile` (`$CODEX_HOME/<name>.config.toml`), `review_model`, `agents.default_subagent_model`; `codex exec --json`, `-c`; MCP via `codex mcp add` (config.toml) or `-c mcp_servers.<id>...` (**inferred, U**) |
| Copilot | `sessionStart` (also `prompt` entries), `postToolUse` (cap 10K), `postToolUseFailure`, `subagentStart` inject `additionalContext`; `preToolUse`, `userPromptSubmitted` (SDK only), `preCompact` do not; `.github/hooks/*.json`, `~/.copilot/hooks/`, inline `hooks` in settings, Claude-compatible `.claude/settings.json` | `--model` (accepts `auto`), `COPILOT_MODEL`, `/model`, `--effort`, `--context`, setting `autoTier`; `copilot -p --output-format json -s --no-ask-user --max-ai-credits N --additional-mcp-config @file` |
| Cursor | `additional_context` from `sessionStart` (fire-and-forget), `postToolUse`, `postToolUseFailure`; `.cursor/hooks.json`; hooks in print mode **U** | `--model`, `/model`, Auto Cost/Balance/Intelligence, subagent `model` (bracket parameters); `agent -p --output-format stream-json -f`; `--api-key` / `CURSOR_API_KEY`; no per-launch MCP flag (U) |
| Gemini CLI | `SessionStart` ("first turn in history"), `BeforeAgent` (this turn only), `AfterTool` additionalContext; `settings.json` `hooks` | `-m`, `model.name`, `/model set`; `gemini -p -o stream-json`; MCP only through `settings.json` (worktree-local `.gemini/settings.json`, inference) |
| Kimi | hook stdout "added to context" (event-specific U); exit 2 stderr fed to the model; `[[hooks]]` | `-m`, `KIMI_MODEL_NAME`, `--config-file`; `kimi --print --output-format stream-json --mcp-config-file <f>` |
| Qwen | plain stdout of `SessionStart`/`UserPromptSubmit`/`UserPromptExpansion` and `additionalContext` of PreToolUse/PostToolUseFailure/PostToolBatch/UserPromptSubmit/SessionStart/PreCompact; hook input has `context_usage`, `context_limit` | `-m`, `fastModel`, `compactionModel`, `agents.builtin.exploreModel`; `qwen -p -o stream-json --approval-mode`; MCP in settings only (U flag) |
| Goose | hook stdout injection **U**; reliable channel `GOOSE_MOIM_MESSAGE_TEXT/_FILE` every turn | `GOOSE_MODEL`, `GOOSE_SUBAGENT_MODEL`, recipes; `goose run -t --output-format stream-json --max-turns`; `--with-extension`, `--with-streamable-http-extension` |
| Kilo | plugin hooks `experimental.chat.system.transform`, `chat.message`, `tool.execute.after` (R/V source); no shell hook config; `AGENTS.md`, `instructions` | `-m prov/model`, `small_model`, per-agent `model`, `--variant`; `kilo run --format json --auto`; `KILO_CONFIG_CONTENT` for MCP |
| OpenCode | same plugin hooks (docs deny confirming `chat.*`); `AGENTS.md`, `instructions` | `-m`, `small_model`; `opencode run --format json --auto`; `OPENCODE_CONFIG_CONTENT` |
| Aider | none; `--read`, `/run`, `--message-file` | `--model`, `--weak-model`, `--editor-model`, `AIDER_*`; `aider -m ... --yes-always`; no MCP |
| Cline | hook output to model **U**; `-s/--system` replaces the system prompt for a run; `--hooks-dir` | `-m`, `-P`, `--thinking none..xhigh`; `cline --json --auto-approve`; MCP file `CLINE_MCP_SETTINGS_PATH` |
| Roo | none in the condense pipeline; slash commands in the first message survive | `--mode`, `-m`, `--provider`, `-r/--reasoning-effort`; `roo --print --output-format stream-json` |
| Amp | `agent.start` hook returns `message` appended to the user message; `agent.end` can return `continue` + `userMessage`; `tool.result` can replace output; need `--plugin-ready-timeout` in `-x` mode | modes `low/medium/high/ultra`, `--fast`; `amp -x --stream-json`; `--mcp-config '{...}'` |
| Grok CLI | **no documented injection**: passive-hook stdout ignored; `--rules <TEXT>`, `--append-system-prompt`, `.grok/rules/*.md` | `-m`, `GROK_DEFAULT_MODEL`, `--effort`, `[subagents.models]`; `grok -p --output-format streaming-json --always-approve --max-turns N`; MCP only via config |
| Windsurf (Cascade) / Devin CLI | Cascade: none documented; Devin CLI `SessionStart` / `UserPromptSubmit` `additionalContext` (V) | Devin CLI `devin -p --model <m> --permission-mode`; Cascade headless U |
| Antigravity | `SessionStart`, `PreInvocation`, `PostInvocation` output `injectSteps` (`systemMessage`, `ephemeralMessage`); `Stop` `decision: "continue"` + `reason` injected; `AGENTS.md` / `.agents/rules` | `--model SLUG` (`agy models`), `--effort low\|medium\|high`, `--agent`; `agy -p ... --output-format json\|stream-json --print-timeout`; MCP `.agents/mcp_config.json`; `modelProvider: "gemini"` + `GEMINI_API_KEY` for API-key mode |
| dsh | `SessionStart` (context, runs detached but arrived before turn 1 in the probe), `UserPromptSubmit`, `PostToolUse`; no pre-compact | `session/set_config_option` (model, reasoning_effort) over ACP; `dsh --profile headless --json`; MCP only via a Cordis patch overlay `--patch` |
| Pi | extension events (`input`, `context`, `before_provider_request`, `tool_result`); `AGENTS.md` | provider/model in `models.json`; `pi --mode rpc\|json`, `--print`; MCP `pi mcp add` with `exposure: direct` |

Closed or cloud agents without descriptor-level facts here (cody, glm, openhands, qodo, replit, tabnine): **U** for every row.
OpenHands has a documented condenser (research/openhands.md); treat all as "no control". Antigravity, `vscode` (Copilot in VS Code) and `devin` (Devin CLI / Windsurf) are no longer in this list: see the rows and pass2 sections 1, 2, 4.1.

## 3. What ddflow can do per agent

Levels: **LAUNCH** (ddflow sets it for a lane it starts: env, flag, `-c`, lane-private config dir), **INJECT** (text reaches the model through a door),
**QUERY** (ddflow can read usage or limit state), **ADVISE** (only the operator or the model can act), **NONE**. Lane-private config files
are written in the lane's own directory, never in the operator's user config (D-agent-parity: nothing is installed or edited without consent).

| Agent | Cache TTL | Compaction window / threshold | Compact action | Re-inject after compaction | Limit signal | Resume | Cheaper model per lane |
|---|---|---|---|---|---|---|---|
| Claude Code | LAUNCH env | LAUNCH env/flag (`--autocompact`, subagent frontmatter) | ADVISE (`/compact <text>`), text via `# Compact instructions` | INJECT `SessionStart:compact` | QUERY hook `StopFailure`, stream event, statusline | LAUNCH `--resume` | LAUNCH flag/env |
| Codex | NONE (U) | LAUNCH `-c` | ADVISE; LAUNCH `compact_prompt` | INJECT `SessionStart:compact` | QUERY (rollout R, `/usage`) | LAUNCH `exec resume` | LAUNCH `-m`, `-p` |
| Copilot CLI | NONE (harness-controlled; 1 h or 24 h) | LAUNCH `--context`, `contextTier`; threshold NONE (fixed 80 %) | ADVISE `/compact <focus>` | INJECT `sessionStart` at start, `postToolUse` after; none right after compaction | LAUNCH `--max-ai-credits`; QUERY SDK `account.getQuota`, `/usage`; exit code U | LAUNCH `--resume` | LAUNCH `--model auto\|ID`, `--effort`, `autoTier`, subagent models |
| Cursor | NONE | NONE (`[context=N]` parameter: CLI use U) | ADVISE `/summarize` | INJECT `sessionStart` / `postToolUse` `additional_context` (nothing after compaction; `preCompact` observes) | ADVISE | LAUNCH `--resume` | LAUNCH `--model` |
| Gemini CLI | NONE | LAUNCH (settings file in the lane's `.gemini/`) | ADVISE `/compress` | none (PreCompress advisory) | QUERY exit 1; `/stats` | LAUNCH `--resume` | LAUNCH `-m` |
| Kimi | NONE (U) | LAUNCH env `KIMI_MODEL_MAX_CONTEXT_SIZE`, `--config-file` ratio | ADVISE `/compact <text>` | PostCompact hook (capabilities U) | QUERY **exit 75**; `/usage` | LAUNCH `-S` | LAUNCH `-m`, env |
| Qwen | NONE | LAUNCH settings (`QWEN_HOME`, project `.qwen/settings.json`) | ADVISE `/compress` | INJECT `SessionStart:compact`, `PreCompact` `additionalContext` | LAUNCH `QWEN_CODE_UNATTENDED_RETRY`, `--max-wall-time`; QUERY exits 53/55, hook `context_usage` | LAUNCH `--resume` | LAUNCH `-m`, settings |
| Goose | LAUNCH env (interactive only; headless forced 5m) | LAUNCH env | ADVISE `/compact`; LAUNCH template | INJECT `GOOSE_MOIM_MESSAGE_*` every turn | ADVISE | LAUNCH `--resume` | LAUNCH env |
| Kilo | NONE (5m fixed) | LAUNCH `KILO_CONFIG_CONTENT` | ADVISE; plugin can replace prompt | plugin `experimental.session.compacting` | QUERY exit codes, `kilo stats` | LAUNCH `-s` | LAUNCH `-m`, config |
| OpenCode | NONE | LAUNCH `OPENCODE_CONFIG_CONTENT` | ADVISE; plugin | plugin | QUERY `stats` | LAUNCH `-s` | LAUNCH `-m` |
| Aider | LAUNCH `--cache-prompts`, keep-alive pings | LAUNCH `--max-chat-history-tokens` | none | `--read` file (never summarised) | ADVISE | no session resume | LAUNCH `--model`, `--weak-model` |
| Cline | NONE | LAUNCH `--compaction agentic\|basic\|off` | ADVISE `/smol` | plugin/hook (`--hooks-dir`; source examples) | QUERY `run_result.usage` | LAUNCH `--id` | LAUNCH `-m` |
| Roo | NONE (Anthropic ephemeral fixed) | settings (IDE); the CLI has no condense flag | ADVISE button | none (first-message slash commands survive) | QUERY `stream-json` | LAUNCH `--session-id` | LAUNCH `-m`, `--provider` |
| Amp | NONE (mode fixed per thread) | NONE (plugin agents: `compactionThresholdTokens`) | NONE (automatic, 90 %) | INJECT `agent.start` plugin | QUERY `amp usage` | LAUNCH `threads continue` | `--fast`, modes |
| Grok CLI | NONE | LAUNCH config.toml (`GROK_HOME`: `auto_compact_threshold_percent`, `[subagents.models]`) | ADVISE `/compact [context]` | none (passive hook stdout ignored) | ADVISE (`/usage` TUI only) | LAUNCH `--resume` | LAUNCH `-m`, `[subagents.models]` |
| Windsurf (Cascade) / Devin CLI | NONE | Devin CLI U; Cascade NONE | ADVISE `/compact` (Devin CLI) | INJECT `SessionStart` / `UserPromptSubmit` (Devin CLI) | ADVISE `/usage` | LAUNCH `-r` / `-c` (Devin CLI) | LAUNCH `--model`, `agent.subagent_model` (Devin CLI) |
| Antigravity (`agy`) | NONE (cache reads visible in `usage`) | NONE | ADVISE `/compact [msg]` | INJECT `injectSteps` at SessionStart / PreInvocation; no compaction event | QUERY `usage` in json, `/usage` text; exit code U | LAUNCH `--conversation`, `-c` | LAUNCH `--model`, `--effort`; **consumer plans: wrapping `agy` in another agent is not permitted** |
| Kiro CLI | NONE | LAUNCH `kiro-cli settings compaction.excludeMessages N`, `chat.disableAutoCompaction` | ADVISE `/compact` | none (steering files) | QUERY `/usage` works non-interactively | LAUNCH `--resume-id` | LAUNCH `--model`, `--effort`, `--agent` |
| Junie | NONE | NONE | ADVISE `/compress` | INJECT `SessionStart` source `compact` (applies on the next prompt) | ADVISE `/stats` (no headless form) | LAUNCH `--resume`, `--session-id` | LAUNCH `--model`, `--effort`, `--max-steps` |
| Continue (`cn`) | LAUNCH `promptCaching: true` (Anthropic, in the lane config.yaml) | NONE (formula, no setting) | ADVISE `/compact` | none | ADVISE | LAUNCH `--resume`, `--fork` | NONE (no launch model flag found) |
| Zed agent | NONE | settings only (`agent.auto_compact.threshold`; GUI agent, no headless) | ADVISE `/compact` | none | NONE | NONE | settings `agent.subagent_model` |
| dsh | NONE | LAUNCH profile patch | ADVISE `/compact` | INJECT `SessionStart` (no compact event) | exit 1 only | LAUNCH resume / ACP | ACP `set_config_option` |
| Pi | NONE | provider config | ADVISE; extension supplies summary | extension | NONE | RPC | provider config |

## 4. Gaps (what ddflow cannot do, and what to file)

1. **No cache-TTL control** on Codex, Copilot, Cursor, Gemini, Kimi (CLI), Qwen, Kilo, OpenCode, Cline, Roo, Amp, Grok, Windsurf, dsh, Pi. ddflow can only keep tools/instructions byte-stable (CC-cache-policy) and keep gaps short (TE-poll-cadence); the 1 h TTL saving of D-TE-Q1 does **not** transfer. Goose's knob is ignored in headless.
2. **No compaction trigger from outside** on every agent except Pi RPC (`compact`); Kilo/OpenCode plugin could expose one (not built). Rotation (fresh lane from the resume pack) is the portable action, as D-CC-2 already says.
3. **No re-injection door after compaction** on Copilot, Cursor, Gemini, Kimi (U), Grok, Aider, Cline, Roo, Windsurf, dsh. Fallbacks in order: the footer on the next ddflow MCP call (`[reinstruct]`), the instruction file (always re-sent on every request on all clients that rebuild the system prompt), `GOOSE_MOIM_MESSAGE_FILE` on Goose.
4. **Limit text and exit codes are U** for Claude (exit), Codex, Copilot, Cursor, Gemini, Qwen, Goose, OpenCode, Aider, Cline, Roo, Amp, Grok, Windsurf, dsh. These need recorded fixtures from a real limit (cannot be produced without spending a limit): ATF-fixtures records what can be recorded (Kimi 75, Kilo 124/1, Gemini 53) and marks the rest `unavailable`, never a pass.
5. **Docs conflict** worth a probe: Kilo `preserve_recent_tokens` (docs 8 000 vs source 15 000), OpenCode `compaction.prune` (docs false vs Kilo true), Copilot session/weekly limit pages (es vs en), `--yes` vs `--yes-always` in Aider, Cursor binary name (`agent` vs `cursor-agent`), Codex subagent isolation (fork vs summary). Pass 2 adds: Copilot `--max-ai-credits` scope (CLI reference "for each response ... resets per user message" vs how-to "in a session") and subagent depth default (settings 6 vs env table 4); Antigravity `--print-timeout` default (headless page 5m vs CLI reference 0s); Junie `--effort` levels (`low,medium,high` vs `minimal...max`); Cline auto-compact doc ("falls back to rule-based truncation") vs SDK source (agentic mode for any model); Roo `condensingApiConfigId` (docs era) vs current schema (absent).
6. **Claude-only advisor inputs** (`prompt_cache_likely_expired`, `estimated_cache_write_usd`, transcript `usage`) have no counterpart elsewhere except Qwen hook input (`context_usage`, `context_limit`, `input_tokens`), Codex rollout `token_count` (R), Cline `usage` events, Amp stream-json `cache_*` tokens and Kilo/OpenCode `stats`/DB. Elsewhere context size is an estimate. Pass 2 adds Antigravity json/stream-json `usage.cache_read_tokens` (cumulative per session in a stream), Copilot SDK `assistant.usage` / `session.usage_info` (`current_tokens`/`token_limit`) / `account.getQuota`, Cursor `preCompact` input `context_usage_percent`, and Qwen `/stats`.
7. File as defects (D-file-ddflow-defects): the `kimi` descriptor still describes kimi-cli (R-kimi, existing P-agent-parity.kimi-descriptor); `adding-a-harness.md` lacks every field proposed below (ATF-descriptor-fields).
8. **Terms-of-service gate** (pass 2): Antigravity consumer plans do not permit wrapping `agy` in another agent or rotating accounts (FAQ); ddflow needs a descriptor fact for it (`[headless].terms`, section 5) so the launcher refuses a consumer-login lane and accepts an API-key or Gemini Enterprise lane. Not researched for the other agents: U (their terms were not read).

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
| *pass 2:* **launch-time spend/budget caps** (Copilot `--max-ai-credits`, Qwen `--max-wall-time` / `--max-tool-calls` / `--max-session-turns`, Grok `--max-turns`, Junie `--max-steps`, Claude `--max-budget-usd`, Roo `allowedMaxCost`) | `[caps]` keys `spend`, `turns`, `tool_calls`, `wall_time`, `steps`; each a setter plus `unit = "usd"\|"ai_credits"\|"turns"\|"tool_calls"\|"seconds"\|"steps"`, optional `min`, `soft = true` | a cap is a cap; no new section |
| *pass 2:* **quota query endpoint** (Copilot SDK RPC `account.getQuota`, Antigravity `agy -p /usage`, Kiro `/usage` in non-interactive runs, Amp `amp usage`, Claude `/usage`) | `[meters].query` becomes string **or** table `{kind = "slash"\|"print"\|"cli"\|"rpc"\|"http", command \| argv \| method \| url, via, format = "text"\|"json"}`; the string form stays valid (= `kind = "slash"`) | one key, one more shape; keeps the existing reserved key |
| *pass 2:* **where per-run usage numbers appear in headless output** (Antigravity `usage.*`, Cline `run_result.usage`, Amp stream-json, Claude stream-json) | `[meters].usage_fields`: canonical name to JSON path (`input`, `output`, `thinking`, `cache_read`, `cache_write`, `total`, `credits`) | feeds the advisor with cache hit data wherever the agent reports it |
| *pass 2:* **reset source variants** | `[meters].reset_source` values `message`, `field:<path>`, `calendar:<rule>` (`utc_month_start` for Copilot credits), `window:<spec>` (`5h+weekly` Antigravity, Claude), `dashboard` | extends the existing key's vocabulary |
| *pass 2:* **premium-request multiplier / model price** | **not a harness fact**: model-family data for CC-inventory (`ddflow/templates/context_families.toml`: input, cached, cache_write, output, long-context threshold and tier, multiplier, `legacy = true` for request-based multipliers). The descriptor only names the refresh source in `[meters].price_source` (Copilot SDK `models.list`, `billing.multiplier`) | the multiplier is obsolete for new Copilot plans (usage-based since 2026-06-01); prices belong with the model |
| *pass 2:* **cache TTL stated but not settable** (Copilot "1 h / 24 h"), cache reads observable | `[tokens.cache]` `control = "harness"`, `ttl_stated`, `observe` (a `usage_fields` name) | three-valued rule: a stated value is not a setter |
| *pass 2:* **context tier** (Copilot `--context default\|long_context`, Cursor `[context=N]`) | `[tokens.window]` `tier = <setter>`, `tier_values` | a tier is a window choice with a price effect |
| *pass 2:* **resident tool schemas** (Qwen `tools.eager`, Copilot `toolSearch`, Kiro `toolSearch.*`) | `[caps]` key `tools_resident = <setter>` | extends the reserved "mcp tool cap" |
| *pass 2:* **terms of automation** (Antigravity consumer plans) | `[headless]` key `terms = "ok"\|"api-key-only"\|"consumer-plans-prohibited"` with a `[verified]` source; the launcher refuses a lane whose auth is outside the allowed set | a legal constraint changes what the launcher may do |

`[tokens]` keys (each is a value, `none = true`, or absent = not verified; a value may carry the HX-facts-core fact record):

```
[tokens.cache]       control = "ttl-env" | "ttl-setting" | "automatic" | "keepalive" | "harness" | "none"
                     ttl_values = ["5m","1h"]; default_ttl = "5m"
                     set = <setter> | {main = <setter>, subagent = <setter>}   # a setter is the launch mechanism below; the table form splits main and subagent
                     headless_ttl_fixed = "5m"                        # Goose: headless forces 5m
                     ttl_stated = "1h"                                # pass 2: a TTL the vendor states but the user cannot set (control = "harness", Copilot)
                     observe = "cache_read"                           # pass 2: the [meters].usage_fields name that shows cache reads
[tokens.window]      set = <setter>  unit = "tokens" | "ratio" | "percent"  min = .. max = ..
                     tier = <setter>  tier_values = [..]               # pass 2: window tier with a price effect (Copilot --context)
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
(`file` is relative to the lane's private config dir named by `[tokens.lane_dir]`, e.g. `env = "CODEX_HOME"`; absent = no lane-private file is possible and file setters are reported unavailable). Placeholders are `{tokens}`, `{ratio}`, `{percent}`, `{ttl}`, and (pass 2) `{credits}`, `{seconds}`, `{n}`, `{bytes}`, `{tier}`, `{slug}`, `{list}` (comma-separated names).
Validation: unknown key = loader error naming key and file (existing rule); a setter without `[verified]` source is shown as `not verified` by `ddflow harness show` and the launcher refuses to apply it
(prints `unavailable`, never silently skips). Every value is overridable in config (D-everything-configurable); an operator override records the source `operator`.

### 5.1 Worked example, six agents (Claude, Codex, Kimi; pass 2 adds Copilot, Antigravity, Qwen)

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

```toml
# ddflow/harnesses/copilot.toml (additions; pass 2)
[headless]
resume = ["--resume", "{session}"]               # an explicit id is needed under -p
model  = ["--model", "{model}"]                  # accepts `auto`
effort = ["--effort", "{effort}"]
output = ["--output-format", "json", "-s"]

[tokens.cache]
control = "harness"                              # the client sets retention; no user setting
ttl_stated = "1h"                                # vendor text: "24 hours for OpenAI models and 1 hour for most others"

[tokens.window]
tier = { argv = ["--context", "{tier}"] }
tier_values = ["default", "long_context"]
threshold = { none = true }                      # fixed ~80 %, no setting found

[tokens.compact]
command = "/compact"
takes_instructions = true                        # /compact [FOCUS-INSTRUCTIONS]

[tokens.lane_dir]
env = "COPILOT_HOME"                             # lane settings.json: autoTier, toolSearch, subagents.agents.<n>.model

[caps]
spend = { argv = ["--max-ai-credits", "{credits}"], unit = "ai_credits", min = 30, soft = true }
tool_output = { env = "COPILOT_LARGE_OUTPUT_THRESHOLD_BYTES", value = "{bytes}" }
tools_resident = { file = "settings.json", key = "toolSearch", value = "true" }

[meters]
limit_signal = "none"                            # exit code and text of a cap stop are U
reset_source = "calendar:utc_month_start"        # AI credits reset 00:00:00 UTC on the 1st
query = { kind = "rpc", via = "copilot-sdk", method = "account.getQuota", format = "json" }
price_source = { kind = "rpc", via = "copilot-sdk", method = "models.list" }
```

```toml
# ddflow/harnesses/antigravity.toml (additions; pass 2; hooks/instructions blocks already exist)
[headless]
terms  = "api-key-only"                          # FAQ: wrapping agy in other agents / account rotation not supported on consumer plans
argv   = ["agy", "-p", "{prompt}", "--output-format", "json"]
resume = ["--conversation", "{session}"]
model  = ["--model", "{slug}"]                   # slugs from `agy models`
effort = ["--effort", "{effort}"]

[tokens.cache]
control = "none"
observe = "cache_read"

[tokens.compact]
command = "/compact"
takes_instructions = true                        # "/compact <message>" summarises then answers

[caps]
instructions = { bytes = 24000, tokens = 20000 } # rules: per file / aggregate budget

[meters]
limit_signal = "message"
limit_match = "AI credits balance is too low"
reset_source = "window:5h+weekly"
query = { kind = "print", command = "/usage", format = "text" }
usage_fields = { input = "usage.input_tokens", output = "usage.output_tokens", thinking = "usage.thinking_tokens", cache_read = "usage.cache_read_tokens", total = "usage.total_tokens" }
```

```toml
# ddflow/harnesses/qwen.toml (additions; pass 2)
[headless]
env = { QWEN_CODE_UNATTENDED_RETRY = "1" }       # strict true/1; 429/529 retried, backoff cap 5 min
resume = ["--resume", "{session}"]

[tokens.cache]
control = "automatic"
set = { file = "settings.json", key = "model.generationConfig.enableCacheControl", value = "true" }   # API-key auth only

[tokens.window]
threshold = { file = "settings.json", key = "context.autoCompactThreshold", value = "{ratio}" }
unit = "ratio"
default_trigger = 0.85

[tokens.compact]
command = "/compress"                            # takes_instructions: absent (hook input has custom_instructions)

[tokens.lane_dir]
env = "QWEN_HOME"

[caps]
wall_time = { argv = ["--max-wall-time", "{seconds}s"], unit = "seconds" }
tool_calls = { argv = ["--max-tool-calls", "{n}"], unit = "tool_calls" }
turns = { argv = ["--max-session-turns", "{n}"], unit = "turns" }
tools_resident = { file = "settings.json", key = "tools.eager", value = "{list}" }

[meters]
limit_signal = "exit_code"
limit_exit_code = 55                             # wall/tool budget; 53 = turn limit
query = "/stats"
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
| Research Antigravity, Roo and closed IDEs further? | **done in pass 2** (pass2 sections 2 and 4); remaining U cells need live agents (ATF-fixtures) |
| Launch Antigravity lanes with a consumer login? | no: FAQ prohibits wrapping `agy` in other agents on consumer plans; accept API key (`modelProvider: "gemini"`, `GEMINI_API_KEY`) or Gemini Enterprise only (ATF-Q8) |
| Set a Copilot `--max-ai-credits` default per lane? | operator-set, no default shipped; the first cap outside Claude, but its scope (per response vs per session) is unverified (ATF-Q9) |

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
Pass 2 (raw sources, see pass2 section 0 and 4): github/docs `content/copilot/**` and `data/{tables,reusables,variables}`; microsoft/vscode-docs `docs/agents/**`, `docs/agent-customization/**`, `blogs/2026/06/17/improving-token-efficiency-in-github-copilot.md`; antigravity.google/docs/{cli/*,models,plans,rules,hooks,subagents,mcp,faq,changelog,sdk/*} and /llms.txt; QwenLM/qwen-code `docs/users/**`; alibabacloud.com/help/en/model-studio/context-cache; Windsurf/Devin, Cursor, Roo, Cline, Continue, Zed, Junie, Kiro, Amp, Grok, Goose pages listed in pass2 section 4.

## 9. Changelog (second pass, 2026-10-23, res-atf2)

One line per changed cell or paragraph; the evidence for each is in [agent-token-features-pass2.md](agent-token-features-pass2.md).

- 2.1 Copilot CLI / VS Code cache: U to V (TTL statement 1 h / 24 h, retention parameter, four breakpoints, busters, per-model cache prices, Auto discount); control stays none (pass2 1.2).
- 2.1 Cursor cache: price ratios added (Sonnet 5.5 1.25x write, 0.1x read; own models no write charge) (pass2 4.2).
- 2.1 Qwen cache: DashScope explicit/implicit split and prices verified; `/stats` cached share; idle clearing and `tools.eager` byte-stability added (pass2 3).
- 2.1 Cline cache: U to V for the source-level `cache_control`/`cachePoint`, user control still U (pass2 4.4). Roo: U to V (hard-coded ephemeral cache, provider toggles) (pass2 4.3).
- 2.1 Amp: mode switch invalidates the cache (V). Grok: routing header names, cached ratio U to V (0.25x grok-4.7). Windsurf: price lines (SWE-2 cache input), control still U. Antigravity: new row, control U, `cache_read_tokens` observable (pass2 2).
- 2.2 Copilot CLI: `--context`/`contextTier`, `chat.agent.maxRequests`, absence of a threshold setting verified. Cursor: bracket parameters, preCompact context fields. Qwen: full knob list verified (`autoCompactThreshold` semantics, idle clearing defaults, retain-files env). Cline: trigger ratio 0.9 and preserve 20k from source. Roo: `autoCondenseContextPercent`, forced 75 %, reserve 30 %, `condensingApiConfigId` gone. Amp: 90 % default, `compactionThresholdTokens`. Grok: `/compact [context]` U to V. Windsurf/Devin: `/compact`, `/context`, `devin.autoContinue`. Antigravity: new row (no knob, `/compact`).
- 2.3 Copilot: PostCompact/SessionStart(compact) absence verified, injection doors named, instruction paths expanded. Cursor: preCompact input fields; rules frontmatter. Qwen: PreCompact `additionalContext` verified. Cline: replacement via SDK plugin/hook examples (was "no hook documented"). Roo: `customSupportPrompts.CONDENSE`. Grok: passive-hook stdout ignored. Windsurf/Devin: `PostCompaction` hook and `additionalContext` (Devin CLI), rule limits. Antigravity: new row (hooks without a compaction event, `injectSteps`, 24 KB / 20 000-token rules budget).
- 2.4 Copilot: `--max-ai-credits`, `chat.tools.compressOutput.enabled`, subagent settings. Cursor: parallel cost note. Qwen: budgets and exits, `tools.eager`, subagent schema trap. Cline: `spawn_agent` details. Roo: preview sizes, `new_task` isolation. Grok: `--max-turns`, `[subagents.models]` (was U). Windsurf/Devin: subagent routing. Antigravity: new row (MCP time limits, subagent tiers).
- 2.5 Copilot: limit/quota U to V (credits reset time, SDK `account.getQuota`, `session.usage.getMetrics`, `--max-ai-credits`, `continueOnAutoMode`); cap scope and stop exit code stay U. Cursor: credit/limit model, Admin API. Qwen: strict retry value, exits 53/55. Cline: usage in `--json`, ClinePass windows. Roo: U to V (cost caps, resume flags). Amp: credit expiry. Grok: `/usage` exists (TUI). Windsurf/Devin: U to V (quota model, `/usage`, resume flags, admin API). Antigravity: new row (windows, `/usage`, `/credits`, exhaustion message).
- 2.6 Copilot, Cursor, Cline, Roo, Grok, Windsurf/Devin: injection and routing cells refined; Antigravity: new row (`injectSteps`, `--model`/`--effort`/`--agent`, MCP path, API-key mode).
- 3 per-agent table: rows updated for the above; rows added for Antigravity, Kiro, Junie, Continue, Zed. Windsurf row renamed Windsurf (Cascade) / Devin CLI.
- 4 gaps: item 5 (docs conflicts) extended; item 6 extended; item 8 added (terms-of-service gate).
- 5 descriptor: placement rows for spend caps, quota query shapes, usage fields, reset sources, price source, harness-controlled cache, context tier, resident tools, automation terms; setter placeholders; worked examples for Copilot, Antigravity, Qwen.
- 7 questions: Antigravity research done; ATF-Q8 (consumer-plan prohibition) and ATF-Q9 (Copilot cap default) added.
