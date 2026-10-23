# Decision proposal: context compaction advice, summary store and cache-aware policy

State: PROPOSAL (nothing is built before CC-decide). Phase P-context-compaction, task CC-research, 2026-10-23.
Evidence and labels: [research/context-compaction.md](../research/context-compaction.md), [research/context-compaction-cache.md](../research/context-compaction-cache.md).

## Proposal

1. **Option B** (research section 8): a pure advisor, a ddflow-generated compaction instruction, a resume pack, a `session.compacted` record with an
   anchor for search, a cache-prefix lint and cache metrics. Delivered through existing channels only (session family action `advise`/`resume-pack`,
   hook `additionalContext`, the `[reinstruct]` footer, the operator digest): no new MCP tool (D-mcp-new-nouns).
2. ddflow advises and shapes; it never compacts a client and by default never blocks PreCompact (B195 rule). Claude Code: no hook can run `/compact`.
   Subagents are rotated (handoff, fresh agent from the resume pack), not compacted.
3. The log is the truth: summaries are extra records with provenance (session, model, time, trigger, what they replaced), redacted before
   storage, displayed inside the data fence, never deleted (D-no-deletion). After every compaction the operator prompts, decisions, leases and next
   step are re-injected from the log (hook-injected context is itself summarized away).
4. Cache policy: ENFORCE byte-stable tool list and instruction text, volatile text last; RECOMMEND one model per conversation, 1 h TTL for lanes that wait,
   compaction while warm before an idle gap or at resume when cold and C >= 200 K. No keep-warm pings by ddflow.
5. Thresholds are data per (harness, model family) with `unknown` as the safe default; prices and TTLs are dated data, never code. One knob set, shared with TE-context-caps.

## Questions for the operator (CC-Q1..Q9, default in bold)

| Id | Question | Default |
|---|---|---|
| CC-Q1 | Option A, B or C? | **B**; C only for lane rotation and Pi |
| CC-Q2 | May ddflow own a `# Compact instructions` region in CLAUDE.md/AGENTS.md (written by `ddflow setup`)? | **yes, a marked region, regenerated, opt-out per project** |
| CC-Q3 | May PreCompact ever block (e.g. while a merge is in flight)? | **no, never by default; opt-in `[compaction].block_during = ["merge"]`** |
| CC-Q4 | Thresholds (suggest/strong/rotate) per family: accept the research table as defaults? | **yes: lanes 150/250/350 K, lead 250/400 K, `unknown` 150/250 K**; same values as TE-context-caps |
| CC-Q5 | Nudge budget: once per level, cooldown? | **once per level, 20 min cooldown, silent while waiting, off for the operator's own session unless asked** |
| CC-Q6 | Subagent past the strong level: rotate automatically (lead launches a fresh lane from the resume pack at the next gate boundary)? | **advise only; automatic rotation after the operator turns it on** |
| CC-Q7 | Store the harness's own summary text (where readable)? | **yes, redacted, pointer-only when opaque (Codex)** |
| CC-Q8 | Retention of stored summaries vs the 90-day transcript default (B-am-retention)? | **summaries follow the session log (kept); chat turns follow retention** |
| CC-Q9 | Cache keep-warm by ddflow (polling)? | **no; 1 h TTL setting instead (TE-subagent-ttl)** |

Answers: none yet. Each is recorded as D-CC-* by CC-decide; thresholds and TTL are coordinated with TE-decide.

## Alternatives rejected

- Option A only: drifts and is forgotten; no timing logic (cold vs warm), no re-grounding.
- Option C on Claude Code: no hook can compact; adds adapters for little gain beyond B.
- Keep-warm polling: dominated by the 1 h TTL for gaps under an hour (research section 4).
- Embedding stored chat text in nudges: injection vector; nudges carry ids and numbers only.

## Would invalidate

Advice ignored more than 80% of the time (fold into TE-context-caps); rising post-compaction rework or repeated operator instructions
(instruction template wrong); hooks no longer firing in subagents (probe in CC-inventory); vendor price ratios changing the break-even (table is dated data).
