# Token spend in ddflow development with Claude Code: where it goes and what to change

Task TE-research (phase P-token-economy), agent res-token-spend, 2026-10-23. Operator request
(verbatim, 2026-10-23): "In parallel - make a research on how the session tokens are spend for the
development of the ddflow with claude. I see the context is 43.7% messages, and I wonder is there a
way to optimize this and to save on tokens somehow? Maybe make the messages more compact, or
something like this? But I might be wrong, so make a research and give me report and proposals".

Labels: MEASURED (computed from the Claude Code transcripts and ddflow itself, window
2026-10-21 09:37Z to 2026-10-23 14:14Z), ESTIMATED (a model fed with measured numbers, assumptions
named), UNVERIFIED (docs read, not reproduced here). Proposals and their state:
`docs/ddflow/decisions/token-spend.md`.

## 1. Answer first

1. **"Messages 43.7%" is a symptom of the lead's long context, not where most of the money goes.**
   The lead session is about **7%** of the weighted spend; the ~98 background subagents are
   **93%** (MEASURED, section 3).
2. **The biggest single waste is not messages at all: it is prompt-cache rewrites in the subagents.**
   Claude Code gives subagents a 5-minute cache TTL even on a subscription (docs, UNVERIFIED by
   experiment, but the transcripts agree: 100% of subagent cache writes are `ephemeral_5m`). A
   subagent that waits more than 5 minutes (a review, a CI run, a `sleep` poll loop, a `ddflow wait`)
   re-writes its whole context on the next call. **52% of all subagent spend is exactly that**
   (277 M tokens re-written in 1 116 events, average 11 minutes of wait, average ~250 K context).
3. **A one-line setting should remove most of it:** `CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL=1h`
   (or `subagentPromptCacheTtl`, Claude Code 2.1.242 or later). ESTIMATED saving: **about half of the
   subagent spend, 45% of the total** (the model below reproduces the measured total within 6%).
4. **Second lever: context size of the long-lived agents.** 12 agents with 300 or more calls are 67%
   of the subagent spend; 39 agents that grew beyond 200 K tokens are 91%. Rotating or compacting a
   lane at 200 K instead of letting it reach 500-800 K is worth another ESTIMATED 40% on top of the
   TTL change (70% together).
5. **For the lead, the operator's instinct is right but small:** the lead ran at a median 402 K
   context, up to 965 K, and compacted only once (at 967 K). Compacting at 400 K saves ~40% of the
   lead's cost (ESTIMATED), which is ~3% of the total. Compact reports, shorter replies and fewer
   nudges are real but are each below 1% of the total. They are worth doing because they are cheap
   and they slow the lead's growth, not because they move the bill.
6. **Do not spend effort on:** brief or rules dedupe (the brief output is 1.1% of subagent tool-result
   tokens), a shorter CLAUDE.md (2.5 KB), the hook payloads (3.7 K tokens once per session).

## 2. Method and caveats

- Sources (read-only, parsed with `python3 -I`, only aggregates were printed, no transcript text was
  copied): the lead transcript `29b9661e-....jsonl` (14 MB, 1 403 assistant entries = 843 distinct API
  calls after de-duplication by message id), 98 subagent transcripts under `subagents/` (12 729
  API calls), `ddflow doctor`, `ddflow metrics`, the Claude Code docs pages `costs` and
  `prompt-caching` (code.claude.com, read 2026-10-23).
- **Tokens by content** are `bytes/4` of the UTF-8 text of each block (a rough rule: code and JSON
  tokenize worse, ~3.2 bytes/token, so shares are right, absolute sizes are ~20% low). The sum is 774 K
  against a 965 K peak context; the rest is the system prompt, tools, memory and tokenizer error.
- **Tokens by billing** are the API `usage` fields (exact): `input_tokens`, `cache_read_input_tokens`,
  `cache_creation_input_tokens` (split into `ephemeral_5m` and `ephemeral_1h`), `output_tokens` (the
  last streamed entry of each message id; the first undercounts).
- **Weighted spend ("units")**: base input = 1, cache read = 0.1, 5-minute cache write = 1.25,
  1-hour cache write = 2, output = 5. These are Anthropic's published price ratios (ASSUMED to hold for
  Sonnet 5.5; the absolute price was not verified). Under a subscription the plan limit may weigh the
  classes differently (UNVERIFIED), so read the shares, not the dollars. Illustration only: at an
  assumed $3 per million base input tokens, 712 M units is about $2 100 for the 2.5 days measured.
- **Models:** every transcript is `claude-sonnet-5-5`; there is no Haiku or Opus data, so nothing
  here measures model choice (UNVERIFIED, section 7).
- **Not covered:** other lead sessions (the second file is 0.2 MB), the external reviewers' own
  usage (codex, kimi, roborev run outside these transcripts), and the 3 `workflows/` runs. One lead
  session is one sample; the lead's hour-by-hour curve is session specific.
- Simulations replay the measured per-call context growth and the measured idle gaps under a changed
  policy; the baseline replay gives 706 M against 664 M measured (+6%), so savings are read as
  +/-10 points.

## 3. Where the tokens go

### 3.1 Lead versus subagents (MEASURED)

| | API calls | cache read | cache write | output | weighted units | share |
|---|---|---|---|---|---|---|
| Lead (1 h TTL) | 843 | 361.6 M | 4.5 M (1 h) | 0.65 M | 48.5 M | 6.8% |
| 98 subagents (5 m TTL) | 12 729 | 2 707 M | 295 M (5 m) | 4.7 M | 664 M | 93.2% |

Lead units: reads 75%, writes 19%, output 7%. Subagent units: reads 41%, **gap rewrites 52%**,
other writes 3%, output 4%.

### 3.2 The lead context (MEASURED)

- 99.4% of the lead's input is re-sent from cache; new content is ~1.7 K tokens per call on average
  (1.4 M over the session) while each call reads 100 K to 965 K. Median context 402 K; hourly mean
  grew from 111 K to 940 K over the first 28 hours; one auto-compaction at 967 140 tokens (to 22 357).
- "Carry share" = tokens added times the number of later calls that re-read them, the quantity that
  actually costs money (a token added early is paid for again on every later call):

| Content in the lead context | added (bytes/4) | share of added | share of carry cost |
|---|---|---|---|
| Bash tool results (`sed`/`cat`/`grep` 40%, git 17%, ddflow show/list/status/gate 30%) | 133 K | 17.1% | 23.4% |
| Subagent final reports (`<task-notification>`, 226 completions, avg 785) | 181 K | 23.4% | 20.0% |
| Lead's own text | 127 K | 16.5% | 12.9% |
| Agent launch briefs (84 launches, avg 1 091, max 2 332) | 92 K | 11.8% | 9.3% |
| Bash commands written | 51 K | 6.6% | 7.8% |
| Operator messages queued as prompts | 28 K | 3.6% | 4.1% |
| Tool lists, skill list, MCP instructions (system reminders) | 26 K | 3.4% | 7.1% |
| `SendMessage` nudges (154, avg 247) | 38 K | 4.9% | 3.3% |
| `AskUserQuestion` (33) | 22 K | 2.9% | 1.7% |

  So "messages" is mostly (a) command output the lead read to supervise, (b) the subagents' reports
  that come back (62% of the report tokens come from agents that reported more than once: 7 agents
  reported 5 to 29 times after nudges), (c) what the lead itself wrote. Nothing in it is one big avoidable blob: the
  20 largest single entries are 2.3 K to 7.3 K tokens; the biggest are tool-list deltas and skill listings.
- Idle expiry: 6 cache rewrites of 0.34 to 0.79 M tokens each (3.1 M tokens = 6.2 M units, 13% of the
  lead, 0.9% of the total), every one after a gap of 1.0 to 8.6 hours. Nothing else large was ever
  re-written, so tool-list changes and the `list_changed` / soft-upgrade path did NOT bust the lead's
  cache in this window (MEASURED: no large write outside the 6 idle gaps).

### 3.3 The subagents (MEASURED)

- Median agent: 66 calls, ~1.2 M units; first call already carries a **median 52 K tokens**
  (`ddflow doctor` attributes 28.8 K to ddflow: `tools/list` 26.8 K, brief 1.4 K, instructions 0.5 K;
  the rest is Claude Code's own prompt, CLAUDE.md, memory, skills). Start-up prefix reads + first
  write are ~11% of subagent spend; ddflow's share of that is about 6%.
- Concentration: top 10 agents = 63% of spend; 12 agents with 300 or more calls = 67%; 60 agents with
  under 100 calls = 7.5%. By role: 40 "fix" lanes 56%, 4 "implement phase" agents 33%, 14 research 3%,
  2 reviewers 0.7%. Peak contexts: 39 agents above 200 K (91% of spend), 17 above 300 K (74%), 6
  above 500 K (45%), max 809 K. Lanes accumulate every item they handled.
- **Waiting is the cost** (calls whose cache write was at least 50 K tokens after a gap of at least
  5 minutes: 1 116 events, 277 M tokens, 94% of all subagent cache writes; only 3 big writes happened
  without a gap). By the tool that was running just before the wait:

| Before the wait | events | tokens re-written | avg wait | share of subagent spend |
|---|---|---|---|---|
| claim/heartbeat + `sleep`/poll loops (background job + `for ... sleep 8..20` capped near 570 s) | 583 | 163 M | 11.7 min | 31% |
| `ddflow review` (rubber_duck, critic, roborev) | 276 | 58 M | 11.0 min | 11% |
| `ddflow wait` | 78 | 15 M | 15.7 min | 3% |
| `gate run` / ci / `unit_tests` / pytest | 125 | 29 M | 12 min | 5% |
| other (show, complete/merge, git, Monitor) | 54 | 12 M | | 2% |

  The 570-second poll cap is the worst possible value for a 5-minute cache: every poll cycle outlives the
  TTL, so each one pays a 1.25x rewrite of the whole context instead of a 0.1x read. Ten minutes of
  waiting costs about 4x more as one 10-minute call (one 1.25x rewrite) than as three 3.3-minute
  calls (three 0.1x reads).
- Tool results inside subagents: 4.76 M tokens in 12.7 K results (avg 374). By source: file reads and
  greps 39%, `ddflow show/list/status/brief/recall` 21% (1.0 M), review 7%, pytest 5.5%, claim and
  heartbeat 5%, `sleep` polls 4.4% (1 655 calls), gate runs 4%, git 3.4%, web fetches 1.8%. 35 results are
  over 5 K tokens (215 K total). The biggest single result is 7.5 K. No single command dominates;
  the spread says "bound every output", not "fix one command".
- Failure stops: 15 subagent runs and 2 lead turns ended with the monthly spend limit message
  ("You've hit your monthly spend limit"). Each resume re-wrote the context after a median 13-minute
  gap: 5.5 M tokens = 6.9 M units = 1.0% of the total, plus the lead's resume nudges. The real loss
  is time and attention, not tokens.
- Review economy (`ddflow metrics`, 1 186 items): median 4 review rounds per item, p90 8, max 26;
  rubber_duck finds something in 57% of runs, critic in 55%; ci fails 32%; per-gate time p50 6-10 min.
  That is where the 11% review-wait share above comes from. Rework (a completed item hit by a later bug)
  is 2%, so the gates do buy quality; the lever is fewer wasted rounds, not fewer gates.

## 4. Is "messages 43.7%" the lever?

No for money, partly yes for the lead's own growth.

- The `/context` "Messages" bucket is everything in the conversation after the fixed layers (system
  prompt, tools, memory, skills). In a supervising lead it is by construction large and it grows with
  every turn; it is the thing that gets re-read on every call, so it is *why* the lead's per-call cost
  rises from a 111 K to a 940 K context over a day. But the lead is 7% of the spend, so even removing
  half of its messages (an extreme) would save about 3% of the total.
- The money is in (1) subagent cache rewrites during waits (52% of subagent spend), (2) subagent
  context size (reads 41%, and the rewrites are proportional to it), (3) a small tail.
- The operator's idea of compact messages still has value: shorter subagent reports and fewer nudges
  (section 6) keep the lead under a compaction threshold longer and remove the load-bearing
  noise. It is just the third lever, not the first.

## 5. Claude Code behaviour that matters (UNVERIFIED unless marked; code.claude.com, 2026-10-23)

- Cache TTL: the main conversation gets 1 hour on a subscription within plan usage (the lead's
  writes are all `ephemeral_1h`: MEASURED); subagents, workflows, forks, compaction and titles get 5
  minutes. Controls (2.1.242 or later): `CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL` / `subagentPromptCacheTtl`
  for everything outside the main conversation, `CLAUDE_CODE_PROMPT_CACHE_TTL` / `promptCacheTtl` for
  the main one, `ENABLE_PROMPT_CACHING_1H=1` for both, `FORCE_PROMPT_CACHING_5M=1` overrides. A
  subagent definition can carry `experimental.cacheTtl` (2.1.248 or later). Once the plan is exhausted
  and usage credits are being spent, the main conversation drops to 5 minutes (relevant: the operator
  hit the spend limit). One hour costs 2x per write instead of 1.25x; it pays off whenever gaps exceed
  5 minutes, which is the normal case here.
- Compaction: `CLAUDE_CODE_AUTO_COMPACT_WINDOW` (100 K to 1 M tokens) and
  `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` (lower only; stated to apply to main conversation and subagents).
  `/compact` is itself a large request; idle compaction (`idleCompaction`, 2.1.290 or later) can
  compact shortly before the 1-hour cache expires. Compacting at natural breaks is cheaper than
  waiting for auto-compact mid-task.
- Cache busters (docs): model switch, effort change on older models, fast mode, MCP tool-list changes
  when tools are NOT deferred, compaction, upgrade. With tool search (the default) MCP tool definitions
  are deferred and a server connect/disconnect does not bust the cache; that matches the lead
  measurement. `/usage` shows a `Prompt cache (main)` line (misses, last-miss cause), main conversation only.
- Cost guidance: Sonnet by default, Haiku for simple subagents (`model:` in the subagent definition),
  `CLAUDE_CODE_SUBAGENT_MODEL` / "run every subagent on one model", hooks that pre-filter output,
  skills instead of CLAUDE.md for workflow text, delegate verbose operations so only a summary returns,
  lower effort for simple work. Agent teams cost ~7x a single session.

## 6. Findings by theme, with what is already planned

| # | Finding | Evidence | Already in the queue | New |
|---|---|---|---|---|
| F1 | Subagents run on a 5-minute TTL; waits above 5 minutes re-write the context (52% of subagent spend) | 3.3 | none | TE-subagent-ttl |
| F2 | The poll loops are capped at ~570 s, longer than the TTL | 3.3 | B-wait* tasks do not know the TTL | TE-poll-cadence |
| F3 | Lane agents grow to 500-800 K and are never rotated | 3.3 | LS-budget-per-agent (start size only) | TE-context-caps |
| F4 | The lead sits at 400-960 K and compacts once | 3.2 | none | TE-lead-compaction (in TE-context-caps) |
| F5 | Reports return 785 tokens each and 62% are repeat wake-ups | 3.2 | P-operator-digest (operator side) | TE-report-protocol |
| F6 | Outputs are unbounded; no command dominates | 3.3 | MTD-next-hints (hints only) | TE-bounded-outputs |
| F7 | 52 K start per agent, 28.8 K is ddflow's | 3.3, doctor | P-lean-start (LS-worker-mcp-config, LS-lean-profile, LS-budget-per-agent) | link only |
| F8 | Everything runs on one model; no routing evidence | 2 | none | TE-model-routing (measure first) |
| F9 | Review rounds p50 4, p90 8 | metrics | P-batch-gates (BG-*), D-review-rounds-4 | TE-review-economy (reviewer contract) |
| F10 | 17 spend-limit stops, ~1% of spend, plus nudges | 3.3 | B-quota-pause (planned) | TE-spend-resume |
| F11 | No ledger reproduces these numbers | 2 | B-gap-spend (blocked on B-am-ingest) | TE-measure (interim) |
| F12 | Brief output is 1.1% of tool-result tokens | 3.3 | session.brief_max_tokens | nothing: do not prioritize |

## 7. Proposals ranked by saving per effort

Saving is "% of total weighted spend" (712 M units); the second figure is the ESTIMATED range.

| Rank | Proposal | Saving | Effort | Risk | Owner |
|---|---|---|---|---|---|
| 1 | **TE-subagent-ttl**: set the subagent cache TTL to 1 hour (env/setting), have `doctor` check it and `setup`/launch templates emit it | 40-48% | tiny (config, one doctor check) | writes cost 2x instead of 1.25x for bursty agents (already in the model); needs Claude Code 2.1.242 or later; plan limits may weigh writes differently (verify, falsifier below) | operator + ddflow doctor |
| 2 | **TE-context-caps**: lanes rotate (fresh agent, ddflow brief hand-off) at 200-300 K; `CLAUDE_CODE_AUTO_COMPACT_WINDOW` for subagents; lead compacts at 300-400 K at a task boundary | 20-30% on top of 1 (with 1: 64-70% of subagent spend); lead part 3% | small (config + driver rule + a ddflow rotate hint) | lost working state: mitigated by the event log, `ddflow brief` and a ten-line hand-off note | ddflow driver, lead |
| 3 | **TE-poll-cadence**: wait/poll commands return within 240 s (`ddflow wait --max`, no 570 s loops) so the 5-minute cache stays warm; heartbeat only when due | 30-37% if the TTL stays 5 m; ~0 on top of 1 | small | more calls (each a cheap read); do not combine blindly with 1 | ddflow wait, driver |
| 4 | **TE-model-routing**: a `model` per agent role (Haiku-class for mechanical fix and doc lanes, strongest for design and reviews) with an A/B on rounds and rework before it becomes default | 0-30% of fix-lane spend, UNMEASURED (no non-Sonnet data) | medium (eval first) | quality: rounds per item may rise; keep reviewers strong and cross-family | lead + ddflow dispatch |
| 5 | **TE-review-economy**: reviewer prompt contract (one finding set per round, closed findings not repeated), delta rounds, stop-rule at round 4, batching (P-batch-gates) | 2-4% | medium | review quality; already bounded by D-review-rounds-4 | P-batch-gates, reviewers |
| 6 | **TE-bounded-outputs**: `--brief` / default caps and a `... N more` footer on `show`, `list`, `status`, `gate status`, `review`; the agent prompt says "pipe through a bounded command" | 1-3% | medium | an agent may need the cut part: footer names the full command | ddflow surfaces |
| 7 | **TE-report-protocol**: fixed template (verdict, ids, 6 lines, pointer to a file/event), details by reference; wake-ups answered with delta only | 0.5-1%, plus the lead compacts less often | small | lead needs a detail: reads the file on demand | ddflow driver |
| 8 | **TE-spend-resume**: on the spend-limit message agents stop cleanly with a one-line state; lead resumes all with one broadcast after the limit moves | 1% + fewer nudges | small (needs B-quota-pause) | none | ddflow |
| 9 | **TE-cache-hygiene**: keep the tool list and system prompt stable per session, prefer deferral, turn on `idleCompaction`, show `Prompt cache` line in the statusline | 0.5-1% (lead idle rewrites) | tiny | none | lead / docs |
| 10 | **TE-worker-profile** (= LS-worker-mcp-config + LS-lean-profile, link): core tier and strict MCP config for workers | 2-5% (halves ddflow's 6% start share) | in progress | dead tools hide features: profiles already designed | P-lean-start |
| 11 | **TE-measure**: interim `ddflow usage`-style report over Claude Code transcripts reproducing every number in this document, then re-point to B-am-ingest | 0 (enables the rest; proves the savings) | small | a second transcript reader before B-am-ingest: decision TE-Q5 | ddflow |
| 12 | Lead habits (shorter replies, no repeated tables, bounded commands) | 0.3-0.5% | none | none | lead |
| 13 | Brief/rules dedupe | under 0.1% | medium | none | skip |

Combined (1 + 2): ESTIMATED **-65-70% of subagent spend, ~ -62-65% of the total**, from two
configuration-level changes. The remaining items add a few points each; do them for control and
visibility, not for the headline.

### Falsifiers and the cheapest tests

- TE-subagent-ttl is wrong if, after setting the TTL, new subagent transcripts show no
  `ephemeral_1h` tokens, or the count of big writes after 5-60 minute gaps does not fall. Test:
  `claude -p` is not enough (subagents only); launch one `general-purpose` background agent that runs
  `sleep 330` between two tool calls, read its transcript `usage.cache_creation` before and after
  (the aggregate script of TE-measure does this).
- TE-context-caps is wrong if rotated lanes need more rounds or more rework than long-lived ones:
  compare `ddflow metrics` rounds and rework for lanes rotated at 200 K and not rotated, over 20 items each.
- TE-model-routing is wrong if rounds per item or rework rise by more than the price drop; this is
  the one proposal with no data, hence "measure first".

## 8. What to do first (order)

1. This week, no code: set `CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL=1h` in the environment that launches
   Claude Code (settings `env` block) and confirm in the next 20 subagent transcripts (TE-subagent-ttl).
2. Set `CLAUDE_CODE_AUTO_COMPACT_WINDOW` to 300 K for the lead and rotate lane agents at 250 K
   (TE-context-caps); watch rounds per item.
3. Cap polls at 240 s in the driver and `ddflow wait` (TE-poll-cadence) as the fallback for hosts that
   cannot use the 1-hour TTL.
4. Build TE-measure so the next report is one command; ship TE-bounded-outputs and TE-report-protocol
   with the P-operator-digest work.
5. Run the model-routing A/B on the next 20 mechanical fix items.
