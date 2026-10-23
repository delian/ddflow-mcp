# Context compaction: model-family cache facts, break-even arithmetic and other agents

Companion of [context-compaction.md](context-compaction.md) (task CC-research, agent res-compaction, 2026-10-23); same labels
(VERIFIED, MEASURED, ESTIMATED, UNVERIFIED). Section numbers 3-5 are kept so references hold.

## 3. Per model family (cache, window, trigger)

Vendor facts as read on 2026-10-23; prices move, so they live in a data table with date and source (task CC-inventory), never in code.

| Family (harness examples) | Cache semantics | Read / write multiplier | Min cacheable | What busts it | Window and rot | Recommended trigger (proposal, knobs) | Summary style |
|---|---|---|---|---|---|---|---|
| **Claude** (Claude Code, Kilo, dsh over Anthropic) VERIFIED | explicit breakpoints (up to 4) or automatic; TTL 5 m (refreshed free on use) or 1 h | write 1.25x (5 m) / 2x (1 h); read 0.1x, **0.05x on Sonnet 5.5 and Opus 5.5**, 0.025x on Fable 5.1 | 512 tokens (Sonnet/Opus/Haiku 5.5), 1024-4096 on older | tools -> system -> messages hierarchy; any change to tool definitions, model switch, fast mode, images, tool_choice; compaction replaces messages (system layer stays); old effort-change rules per model | 1 M on Sonnet 5+/Haiku 5.5/Fable; auto-compact near 967 K; vendor: "response quality degrades as a conversation grows" | subagent/lane: suggest 150 K, strong 250 K, rotate 350 K; lead: suggest 250 K at a task boundary, strong 400 K, `autoCompactWindow` 500 K as the backstop (single source: TE-context-caps knobs) | structured, anchored (B-am-handoff schema); instructions via CLAUDE.md section or `/compact <text>` |
| **OpenAI** (Codex CLI, Copilot-GPT) VERIFIED for API, blogs for Codex | automatic prefix cache; GPT-5.6+: write 1.25x/30 min or 2.5x/12 h (`prompt_cache_options.ttl`), earlier: no write fee, `prompt_cache_retention` in_memory ~5-10 min or 24h | read 0.1x (0.05x one model) | 1024 visible tokens (5.6+) | model, tools (names, descriptions, schema, order), `reasoning.effort`, `text.verbosity`, `context_management` (compaction), extending a non-breakpoint message | up to ~1 M (GPT-5.5, blog); Codex `model_auto_compact_token_limit` clamped to 90% (UNVERIFIED) | lane: suggest 150 K; Codex keeps 1 summary + <= 20 K recent user messages (UNVERIFIED); server-side compaction item is **opaque and encrypted**: ddflow cannot read or store it, so the handoff must be ddflow's own | handoff-style; Codex `compact_prompt` key (UNVERIFIED) |
| **Gemini** (Gemini CLI, Antigravity) VERIFIED | implicit automatic (>= 2048 / 4096 tokens); explicit objects on 3.x/2.5 only (not Gemini 4); Gemini 4: full previous request cached only if >= 2050 new tokens are added | cached input 0.1x; explicit storage $/M/h (Flash $0.50, 3.1 Pro $4.50) | 2048-4096 | prefix change; small increments on Gemini 4 | Gemini CLI compresses near ~70% (UNVERIFIED) | explicit cache pays only above 0.74 (Flash 3.8) to 2.5 (3.1 Pro) hits/hour of the stored prefix; otherwise rely on implicit + stable prefix | prose OK; keep ids verbatim |
| **DeepSeek** (dsh, DeepSeek harnesses) VERIFIED | automatic disk cache, prefix, best-effort, hours to days, no write fee | hit $0.003 vs miss $0.15 per M (flash): **2%**; pro 3.3% | none stated | any prefix change | 1 M | cost is not the driver (reads nearly free, rewrites cost 1x not 1.25x); compact for quality and speed: suggest 200 K, strong 400 K | whatever the harness does; dsh `compaction-basic`, tool-result pruner |
| **Kimi** (kimi CLI) UNVERIFIED | automatic context cache with discounted hits (docs page did not load) | probe | probe | probe | 256 K class | treat as 150 K strong until probed (CC-inventory) | |
| **Qwen / GLM via Model Studio** VERIFIED (Alibaba docs) | explicit `cache_control` (5 min TTL): create 1.25x, hit 0.1x; implicit: create 1.0x, hit ~0.2x, TTL indeterminate | | 1024 (512 for Zhipu-deployed GLM) | | model-specific | like Claude when explicit, like DeepSeek when implicit | |
| **Closed IDE agents** (Cursor, Copilot, Windsurf, Devin) | no control | | | | product compacts itself | only the resume pack and the instruction text (pasted) | |

Cross-family rules that hold everywhere: stable prefix first, volatile text last, no timestamps or counters in the prefix, one model per
conversation, compaction and cache are separate events (a compaction always rebuilds the conversation layer).

## 4. The arithmetic (ESTIMATED; units as in token-spend.md: input 1, 5 m write 1.25, 1 h write 2, output 5; read r = 0.1, or 0.05)

Let C = context before, S = summary (20 K), O = summary output (8 K), P = layers that stay cached.

- **Warm compaction** costs `r*C + 5*O + 1.25*S` and saves `r*(C-S)` per later call. Break-even calls N*:

  | C | cost (r=0.1) | N* r=0.1 | N* r=0.05 |
  |---|---|---|---|
  | 50 K | 70 K | 23 | 45 |
  | 100 K | 75 K | 9 | 18 |
  | 200 K | 85 K | 5 | 8 |
  | 400 K | 105 K | 3 | 5 |

  Each later *cold* event also shrinks from `1.25*C` to `1.25*S`, so for agents that wait (52% of subagent spend) the real break-even is sooner.
- **Cold compaction at resume** costs about `1.0*C + 5*O + 1.25*S`; the alternative rebuild costs `1.25*C` (5 m) or `2*C` (1 h write).
  At C = 100 K: 165 K vs 125 K/200 K; at 200 K: 265 K vs 250 K/400 K; at 400 K: 465 K vs 500 K/800 K. So at resume with C >= 200 K compaction is
  free relative to rebuilding and every later call is 10x cheaper; below ~100 K just resume.
- **1 h versus 5 m TTL:** the hour costs an extra `0.75*C` per write and saves `1.25*C` per avoided rewrite, so it pays when a context sees at
  least 0.6 gaps over 5 minutes (and under 60) per write. Measured: subagents see about one per 6 calls (TE-subagent-ttl).
- **Keep-warm polling** (a read every 4.5 min) beats a rebuild only for gaps under `1.25*4.5/r` = 56 min (r=0.1) or 112 min (r=0.05) and
  is dominated by the 1 h TTL. Never recommended except where 1 h is unavailable; ddflow must not ping (it cannot: only the conversation's own request refreshes the entry).
- **Wake or fresh?** A stopped subagent woken by SendMessage reuses its cache if inside the TTL (VERIFIED: resumed subagents can read the cache the original run warmed);
  after the TTL it pays `1.25*C`. A fresh agent pays about `1.25*(P+resume pack)` = 60-80 K. So wake a lane with C > ~70 K only inside the TTL; otherwise start fresh from the resume pack.
- **Batching launches:** parallel subagents of one type in one directory share the system+tools prefix (~52 K, VERIFIED same-directory prefix sharing): the first writes at 1.25x, the
  rest read at 0.1x if started inside the TTL and the first response has begun (concurrency rule). ESTIMATED saving about 59 K units per extra agent, under 1% of total: do it by launch order only.

## 5. Compaction and cache across other agents (what to borrow)

| Source | Strategy | Borrow |
|---|---|---|
| Claude Code (VERIFIED) | auto-compact near the window; `/compact <focus>`; `# Compact instructions` in CLAUDE.md; re-reads <= 5 recent files; SessionStart:compact re-injection; `idleCompaction` before the 1 h cache expires; `/rewind` summarize-from-here | the instruction section, the re-injection door, idle compaction (TE-cache-hygiene) |
| Anthropic API (VERIFIED) | context editing: `clear_tool_uses` (trigger 100 K tokens, keep 3, `clear_at_least`, `exclude_tools`), `clear_thinking`; server compaction with custom summarization prompt and keep-recent-turns; memory tool; clearing invalidates cache so `clear_at_least` | **reference-and-clear**: tool results are cheap to drop when they are already in the store (B-am-ingest); use `clear_at_least` logic as the "worth the rebuild" test |
| Codex CLI (blogs) | summary + <= 20 K of recent user messages; handoff-style prompt; encrypted server compaction | handoff framing for the instruction text |
| OpenHands (docs) | condenser: keep first N events, summarize the middle, max size 120-240 events | pin first (brief/claim) and keep the claim state verbatim |
| Pi (VERIFIED, research/pi-dev.md) | `session_before_compact` extension may supply the summary; originals kept | the only harness where option C (ddflow supplies the summary) works |
| dsh (VERIFIED, research/dsh.md) | `compaction-basic`, tool-result pruner, image offload; no PreCompact event in bridges | action = instruction text only |
| Cline, Aider, Cursor, Letta (UNVERIFIED, memory) | condense/new-task tools; Aider summarizes old chat with a weak model past a token cap and uses a repo map; Letta self-edited memory blocks + archival search | memory blocks = ddflow's brief + search; weak-model summarization = routing hint |
| Factory probe study (cited in B-am-handoff) | structured anchored summaries beat free text (3.70 vs 3.44/3.35); artifact trail is weak (2.2-2.5/5) | file trail from `git diff`, never from model memory |
| Context rot (Chroma, secondary sources) | every tested model degrades with input length; middle of context worst | quality is a second reason to rotate before the window |

