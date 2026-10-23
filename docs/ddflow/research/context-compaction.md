# Context compaction advice, a searchable chat store and a cache-aware policy

Task CC-research (phase P-context-compaction), agent res-compaction, 2026-10-23. Operator request
(verbatim, 2026-10-23): "Make a research for automated proposal from the MCP to the subagents and the agent for context compaction (assuming ddflow logs all the chats and will log and store the summary so they will be available for the agents to search) for the agents depending on the model they use and the tasks, to keep the token usage low, and also depending on the model how to reuse best the token cache to optimize the cost. See what we plan to do, and add on top of it".

Labels: VERIFIED (read in vendor docs on 2026-10-23, `code.claude.com` and the vendor API docs), MEASURED
(`research/token-spend.md`, same date), ESTIMATED (arithmetic on named inputs, section 4), UNVERIFIED (memory,
blog or a page that did not load; needs a probe). Proposals and options: `decisions/context-compaction.md`.
Read first: [token-spend.md](token-spend.md) (where the tokens go; this note does not repeat it).

## 1. Answer first

1. **ddflow can advise, not compact.** In Claude Code no hook can run `/compact` or replace the summary: PreCompact can only block
   (exit 2 or `decision: block`) and receives no summary; PostCompact has no decision control; SessionStart with source `compact`
   can add context; `additionalContext` is accepted by SessionStart, UserPromptSubmit, PostToolUse, SubagentStart/Stop (VERIFIED).
   The model cannot type a slash command. So "automatic compaction proposal" means: (a) ddflow computes a level and the exact action
   for that harness, (b) the lead or operator executes it (`/compact <text>`), (c) for subagents the action is *rotate* (finish, hand off,
   a fresh agent starts from a resume pack), and (d) ddflow shapes what an automatic compaction keeps through the one channel it
   controls: the `# Compact instructions` section of CLAUDE.md/AGENTS.md (documented, VERIFIED) and the SessionStart:compact re-injection.
2. **Hook-injected context is itself summarized away** ("Context that hooks added earlier: summarized with the rest",
   VERIFIED), so every compaction must be followed by a re-injection from ddflow's store. That is the whole value of the store: the
   summary may be lossy because the operator's words, decisions, leases and next step are re-derived from the log, not from the summary.
3. **Compact while warm, before an idle gap; compact at resume when cold and large.** The brief's hypothesis ("compact when the cache is already
   cold") is true only in the second form. Claude Code's summarization request repeats the system prompt, tools and history and
   "reads your prefix from the cache" while warm; "after a break longer than the cache lifetime ... reprocesses that history as uncached
   input" (VERIFIED). Warm compaction of 400 K costs about 105 K units and pays back in 3-5 calls; cold compaction costs about what the
   unavoidable rebuild costs (section 4), so it is the right moment only for a large context you must resume anyway.
4. **The cheapest cache policy is stability, not heartbeats.** One-hour TTL (TE-subagent-ttl) beats keep-warm polling for every gap
   under an hour; polling below 240 s (TE-poll-cadence) is only the fallback. ddflow can *enforce* a byte-stable tool list and
   instruction text (lint on the interface digest) and *recommend* everything else (model, TTL, effort are the harness's).
5. **Most of this is already planned.** 13 existing tasks cover capture, ingestion, search, handoff, restart brief, resume, retention,
   budgets, TTL, rotation and metrics. New on top (section 8): a per-(harness, model family) *advisor* as a pure function, the compaction
   instruction template, a `session.compacted` event linking advice to outcome, a resume pack builder with a hard budget, a cache-prefix
   lint, wake-or-fresh and batching rules, cache metrics that detect regressions, and the operator-digest line. 15 small tasks.

## 2. What ddflow already plans or has (reuse, do not rebuild)

| Need | Existing id | State | What it gives | Gap this research fills |
|---|---|---|---|---|
| Pre-compaction tail note, SessionStart:compact hand-back | B195, `services/compaction.py`, `[session].compaction_digest_chars` | done | 2000-char redacted tail as a session note; hand-back after compaction | tail only, not structured; no advice |
| Hook entry points and canonical events | B-hx-hook-core, B-hx-hook-writers; claude.toml inject = session_start, prompt | done | one handler per event | post_tool not in `inject`; prompt door bug B1878cc0da3 (delivers 0 bytes) |
| Transcript reader and local turn store (FTS5) | B-am-ingest (+ usage per message, redaction, `<private>`) | open | the ONE reader; model and usage fields | consumers below |
| Descriptor `[transcripts]`, PostCompact/SubagentStart/Stop events | B-am-harness-probe | open | paths, `isCompactSummary` marker, retention days | model-family facts are not harness facts |
| Capture of briefs, reports, subagent stop | B-am-capture | open | dispatch/report records | none |
| Structured handoff incl. harness summary, round-trip verify | B-am-handoff (D-agent-memory-defaults: 6 KB) | open | deterministic skeleton + narrative + `isCompactSummary` text | trigger policy; instruction text |
| Previous-run section in the brief | B-am-restart-brief | open | handoff + see-also, fenced, budgeted | resume pack of *live state* |
| Search of old context | B-am-search, B-am-session-cli | open | `transcripts` source, filters, timeline | anchor "before the compaction" |
| Retention, caps, privacy | B-am-retention (500 MB, 90 d, `<private>`), SE-sensitivity (done) | open/done | prune, forget, egress | none |
| Resume after crash | DX-resume-command, DX-recover-brief-slice (D-orphan-recovery) | open | resume brief: commits, owed gates, last failure | compaction/limit-stop is also a resume case |
| Cross-session transfer | B-sc-transfer, B-sc-tree | open | addressed pack | none |
| Re-send after compaction, periodic re-instruction | RQM-doors, D-requirements-injection (charter re-sent after compaction) | open | post-compaction door, shown-ledger | same door carries the resume pack |
| Cadenced footer (the only server-to-agent channel on MCP) | `[reinstruct]` + `services/obligations.py` (every_calls 12, every_seconds 240) | done | specific, non-nagging footer | one more obligation kind |
| Start size and per-agent budgets | LS-measure (done), LS-budget-per-agent (running), D-session-start-budget | | effective = min(global, per agent type) | start size only |
| TTL, rotation, polls, reports, bounded output, measure | TE-subagent-ttl, TE-context-caps, TE-poll-cadence, TE-report-protocol, TE-bounded-outputs, TE-measure, TE-cache-hygiene | open | see token-spend.md | advisor engine, harness/model table |
| Tool-list stability and re-announce | SU-digest (done), SU-announce, SU-client-table (done), SU-facade-mode, MTD profiles | | interface digest, client table | cache-aware swap timing |
| Routing and switch cost | D-model-routing, GV-switch-cost, TE-model-routing, GV-roles | open | switch only at safe points, cost preview | cache facts as input |
| Spend ledger | B-gap-spend, B-quota-meters, GV-step-cost | open | usage per lane/item (UNAVAILABLE not zero) | cache-specific metrics |
| Operator queue/digest | OD-* (P-operator-digest) | open | "waiting on you" | compaction line |
| Other harness compaction hooks | PI-extension (`session_before_compact`), PI-session-ingest, DSH-session-ingest, OpenHands condenser idea (research/openhands.md #6) | open | | per-harness action |

D-no-deletion holds: compaction never deletes log or transcripts; the summary is one more record and the pre-compaction messages stay in
the harness file until its own cleanup (30 days: B-am-ingest imports before that).


Sections 3-5 (per-family cache facts, the arithmetic, other agents) are in [context-compaction-cache.md](context-compaction-cache.md).

## 6. Design (all additions; ids in section 8)

**Advisor** (pure function, `core/`): `advise(harness, model, usage, task_kind, role, idle_s, ttl_s, state) -> {level, action, instruction_ref, why, ttl_note}`.
Inputs: `harness` and `model` from the client info and session start declaration (model also from the transcript's last assistant entry); `usage` is *measured* where the transcript has
it (last assistant `usage`: input + cache_read + cache_creation = the context size, VERIFIED field names) else estimated as bytes/4; `idle_s` from the transcript timestamp or the MCP connection's last call;
`ttl_s` from env/setting knowledge (TE-subagent-ttl) or the family default. Levels: `none`, `suggest`, `strong`, `now`. Level comes from the family table (section 3) scaled by
the effective budget; the break-even of section 4 can lower it (expected remaining calls from the item's gate state: gates left x ~6 calls) but never raise it above the table.
Task-kind rules (section 7) shift thresholds. Action per harness: Claude lead -> "run `/compact <text>` now (cache warm, N s since last response)" or, when
cold and C >= 200 K, "compact on your next prompt, before reading anything"; Claude subagent -> "finish the current item, `ddflow session handoff`, return; the lead starts a fresh agent"; Codex -> `/compact` +
`compact_prompt`; Pi -> extension supplies the summary; closed IDE -> text only. Hysteresis: a level fires once per crossing; after compaction the counter resets.

**Instruction text** (<= 150 tokens, one template, rendered per harness): keep verbatim the operator's instructions in this session (ids from `ddflow session prompts`), item ids and worktree
paths held, decision ids in force, pending approvals, the next step; keep pointers (file:line) not contents; drop tool output, diffs, logs, failed attempts already recorded; end with "everything else is in ddflow:
`ddflow brief --resume`, `ddflow search`". Because the log, not the summary, holds the truth, a lossy summary is recoverable.

**Delivery** (no new tool, D-mcp-new-nouns): (1) action `advise` on the existing session family (CLI `ddflow session advise`), (2) a footer obligation in the `[reinstruct]` channel for MCP-only clients, (3) hooks:
PostToolUse/UserPromptSubmit `additionalContext` once per level with a per-session cooldown (not nagging, same rule as obligations.py: specific and stops when dealt with), SubagentStart carries the resume pack, SessionStart
`compact|resume` carries the resume pack and, on resume, uses the payload's `seconds_since_last_response`, `context_tokens`, `prompt_cache_likely_expired`, `estimated_cache_write_usd` (VERIFIED, v2.1.251+) as advisor input, (4) the operator digest line for the lead
(the lead cannot run `/compact` itself, the operator or the harness does).

**Store** (mostly existing): operator prompts and handoffs committed, turns local (D-agent-memory-defaults). New: one `session.compacted` event {trigger manual|auto, context tokens before/after, cache warm|cold, advice id,
instructions sha, summary pointer}, so advice can be scored against outcome; the anchor `--before-compaction` for `session search/prompts`, giving "recall my instructions from before the compaction" as a deterministic list of recorded operator prompts, not a model answer.
Redaction: everything through `redactor('log')` + `<private>` strip before storage, stored text is DATA inside the provenance fence, and the advisor/instruction text never embeds stored chat text. An opaque summary (Codex) is stored as a pointer only.

**Resume pack** (<= 1500 tokens, deterministic, no model): claimed items and leases with worktree and branch, last gate state per item, owed gates, last 5 decisions in force (ids + titles), open approvals, last 3 operator prompts verbatim, the handoff
skeleton if present (B-am-handoff), exact next command. It is the compaction re-injection, the 429/spend-limit resume (the pattern seen on 2026-10-23: agents re-read everything), and the fresh-lane start. It composes DX-resume-command and B-am-handoff; it does not replace them.

**Cache policy** (enforce vs recommend): ENFORCE (ddflow owns the bytes): tool list sorted and byte-stable per (version, profile, config), no dynamic text in MCP `instructions`/tool descriptions, volatile text only in the conversation layer, deterministic standard brief with
the per-item part last; a swap of the tool list for a client that does NOT defer MCP tools waits for a cache-cold moment (idle > TTL, compaction or restart) unless it is a hard upgrade. RECOMMEND (harness owns): one model per conversation (a switch rebuilds C), TTL, effort, fast mode, `idleCompaction`.
Claude Code defers MCP tools by default, so tool-list changes did not bust the lead's cache (MEASURED in token-spend.md section 3.2).

## 7. Per task kind

| Kind | Typical context | Policy |
|---|---|---|
| research | large reads | read through a bounded command (TE-bounded-outputs); suggest at 150 K; write findings to the doc/evidence as you go so rotation loses nothing; a reader subagent returns <= 6 lines |
| implement | diffs, test output | keep diff pointers (`git diff --stat`, file:line), never paste diffs back; compact at gate boundaries (after merge), not mid-edit; rotate after `complete` |
| review (rubber_duck/critic/roborev) | rounds | fresh context per round is cheaper than carrying prior rounds when the prompt re-supplies the open findings (round p50 4: carrying 4 rounds is about 1.25*C each after 11-min waits); delta rounds exist |
| wait/poll | no growth wanted | bounded waits <= 240 s (TE-poll-cadence); with 1 h TTL a long wait is a single call; advisor level `none` while waiting, compaction only after the wait returns |
| lead/coordinator | grows to 400-960 K | reports by reference (TE-report-protocol); compact at task boundary at 250-400 K; operator-visible digest line; idle compaction before the hour expires |
| merge/complete | short | rotate point: finish, hand off, new lane |

## 8. Options and recommendation

| | A: guidance only | **B: advisor + resume pack + store (recommended)** | C: B + automatic hook-driven compaction |
|---|---|---|---|
| What | doc text, TE env settings, `# Compact instructions` by hand | A + pure advisor, instruction template, resume pack, `session.compacted`, cache lint, metrics, digest line | B + ddflow-initiated compaction where a harness allows |
| Savings (token-spend ESTIMATE) | 1 h TTL + caps by hand: most of the 62-65% | B adds consistency, correct timing (cold/warm), no lost context; 3-8 points beyond A (ESTIMATED, unproven) | little beyond B on Claude Code: no hook can compact; works on Pi (and dsh-type) only |
| Risks | drifts, forgotten | advisor noise; summary quality; maintenance of the family table | blocking compaction mid-tool-call; summaries stored without review |
| Cost | none | 15 small tasks, no new tool | + per-harness adapters (PI-extension) |

Recommendation: **B**, rollout in four steps: (0) no code: TE-subagent-ttl and TE-context-caps settings plus the `# Compact instructions` text; (1) advisor + instruction + MCP action + hooks (cooldown); (2) resume pack + `session.compacted` + anchor (after B-am-ingest/B-am-handoff land; until then the pack uses the log only);
(3) cache lint + metrics + digest. C is limited to lane *rotation* (already TE-context-caps) and Pi. **What would invalidate B:** advice ignored >80% of the time (then fold into TE-context-caps), post-compaction rework or re-asked instructions rising (instruction template wrong), a harness update making hooks stop firing in subagents (probe CC-inventory), or vendor price ratios changing the break-even (table is data, dated).

## 9. Metrics and regressions

From `usage` rows (B-am-ingest; TE-measure until then): hit ratio `read/(input+read+creation)` per session and agent; rewrite events (creation >= 50 K after a gap >= TTL, as in token-spend 3.3) by preceding tool; **unexplained busts** (creation spike with no gap, no compaction, no model change: the cause list is ddflow's: interface digest changed, profile switch, version upgrade);
cost per merged item (B-gap-metrics); per compaction: C before/after, summary tokens, files re-read in the next 10 calls, operator prompt repeated (textsim to an earlier prompt: B-am-loops), rounds per item for rotated vs unrotated lanes (the TE-context-caps falsifier). Claude Code also shows `Prompt cache (main)` in `/usage` and `prompt_cache` in the status line (v2.1.251+, main conversation only). Regression: hit ratio down more than 10 points week over week, or unexplained busts > 1 per day, doctor warns and the digest names it.

## 10. Risks

| Risk | Mitigation |
|---|---|
| Summary drops an operator instruction | operator prompts and decisions are in the log; the resume pack re-injects the last prompts verbatim; the summary must cite ids; probe questions (B-am-handoff verify) |
| Secrets in chats | redact before store (log profile, `<private>`), egress filter on search, local files 0600, `session forget`; export uses the export profile |
| Stored text is an injection vector | provenance fence on every display; the instruction text and nudges contain no stored text |
| Auto-compaction races a tool call | ddflow never blocks PreCompact by default (B195 rule: a hook never blocks); advice fires at boundaries, not mid-call |
| Advisor noise | once per level, per-session cooldown (default 20 min), suppressed while waiting, silent below the table; measured ignore rate |
| Wrong family facts | data with source and date, `unknown` is a first-class value, a probe per row; prices never in code |
| Opaque (encrypted) summaries | store pointer only, rely on ddflow's own handoff |
| Lead cannot compact itself | action is addressed to the operator via the digest, plus the `autoCompactWindow` backstop |

## 11. Open probes (UNVERIFIED, owned by CC-inventory)

Do PostToolUse/UserPromptSubmit `additionalContext` reach a *subagent*? Does PostCompact carry the summary (third-party issues say so)? Does the compaction request write a cache entry when cold? Kimi cache semantics and prices; Codex defaults; whether `prompt_cache_options` exists on the account's models; Claude Code `PreCompact` input fields beyond `trigger`.

Sources: code.claude.com docs (hooks, costs, prompt-caching, context-window, model-config), platform.claude.com (prompt-caching, context-editing, compaction), developers.openai.com (prompt-caching, compaction), ai.google.dev (caching, pricing), api-docs.deepseek.com (kv_cache, pricing), alibabacloud.com Model Studio context cache, docs.openhands.dev condenser, third-party Codex blogs and Chroma context-rot coverage (secondary).
