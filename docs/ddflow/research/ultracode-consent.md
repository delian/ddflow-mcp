# Ultracode, part 2: consent, cost, host detection, other agents

Status: research only, 2026-10-27. Extends [ultracode.md](ultracode.md) (task DX-ultracode-research, merged). That file is 35 KB, over the 20 KB cap, so this extension is a separate file. Labels: VERIFIED (read on code.claude.com 2026-10-27, `/docs/en/workflows` in full and `/settings-reference` first 100 KB of 442 KB), CODE (read in this repo, HEAD 4aee0038f), ESTIMATED (arithmetic on named assumptions), UNVERIFIED.

Operator questions (2026-10-27): (1) can ddflow use Claude dynamic workflows from time to time WITH THE USER'S CONSENT, because they can be expensive; (2) can ddflow take the ideas to other coding agents and LLMs.

## 1. Answers first

1. **Yes to (1), as a gated verb, off by default.** ddflow can ask a person before a run and carry the plan digest into the approval. It cannot make Claude Code stop at the ddflow gate; the Claude side has its own prompt, and the two must not be confused (section 2.4).
2. **Yes to (2), in two tiers.** Hosts with native subagent fan-out can run the same recipe as a prompt pattern. All others need the `program` runtime from ultracode.md section 5. No other host has a model-authored, resumable script runtime (section 7).
3. **A ddflow-initiated run cannot use the keyword.** `ultracode` counts only in human-typed prompts. ddflow must launch a saved workflow (`/<name>` or `Workflow(<name>)`) or have the human type the phrase (VERIFIED, 2.3).
4. **ddflow has no cost cap for a Claude workflow run.** Claude documents none either. The cap has to be ddflow's own: agent count and a projected-token estimate in the approved plan, checked before the launch (VERIFIED for Claude, CODE for ddflow).

## 2. Consent design, reusing the approvals engine

### 2.1 What the engine already gives (CODE)

- `ddflow/services/approvals/sites.py` is the table of gated verbs. A row is `(verb, family, tier, min_level, digest, functions, command)`. Tiers: T1 standing policy, T2 a person approves each instance, T3 signed only. Levels: L1 terminal, L2 harness-attested, L3 chat or one-time code, L4 signed. T2 accepts L1 to L3. `tests/test_approvals_sites.py` fails when a refusal site has no row, so a new verb must be added here.
- Request, answer and consume ride on `services/ask`. A consumed approval is single-use, bound to the exact canonical command and its state, and expires (`use_ttl_s`). The agent re-runs the command with `--approval ID`; a person answers in chat (D-RA-Q1..Q12).
- Standing rules (`services/approvals/policy.py`) are registered in code, one verb each, ship OFF (`[approvals].policy = []`), are capped by `policy_max_uses` per item and target, are never above L1, and are recorded as `policy:<id>`, never as a person.
- `approval.py` `check`: a changed digest is not approved. That is the property wanted: if the plan grows from 10 to 40 agents, the old approval does not carry over.

### 2.2 The new verb

Add one site row: `program.launch.claude_workflow` (working name), family A (agent refused outright), tier T2, level L1 or stronger (a chat answer, L3, is the realistic path for an unattended agent). Its digest binds the **plan**, not the script text:

| Field in the digest | Why |
|---|---|
| Host and runtime: `claude-code`, version if known | The approval is for this runtime |
| Workflow name and script digest (saved) or the prompt text (explicit) | A different script is a different ask |
| Planned agent count, maximum concurrency | The dominant cost driver |
| Writer or read-only, and for writers the item and declared globs | Writers need a claim (ultracode.md 4.3) |
| Model per stage, `inherit` shown as the session model | Cost and reviewer independence |
| Projected tokens and projected weighted units (section 3) | The number the person is consenting to |
| Cache TTL in force (5m or 1h) | Changes cost by up to 2x on writes |
| Per-run cap and the day's remaining cap | So the person sees the ceiling |
| Expiry | `use_ttl_s`, default short (an hour is plenty); a stale estimate is not consent |

The chat text the engine renders (`request.py`) would read like: "Launch workflow `ddflow-research-sweep`: 18 agents, read-only, sonnet-5-5, about 5.4M tokens (about 6M weighted units, ESTIMATED), 5m cache, cap 8M, day total so far 11M of 40M. Approve?" A short digest of that text is what is bound.

The record carries the estimate, the cap and later the actual, so over-spend shows in `approvals log` and doctor.

### 2.3 Knobs: standing consent

One setting, `[program.claude_workflow].consent`, with values:

| Value | Behaviour |
|---|---|
| `never` | ddflow never launches or suggests a Claude workflow. Default for a new project |
| `ask` | Every run needs a T2 approval of the plan digest |
| `ask_above = N` | Runs of at most N agents proceed on a standing rule; larger runs ask. Needs a registered policy rule, which is code (`policy.py`), not config alone |
| `auto_readonly_below = N` | Read-only plans under N agents and under the token cap run on a standing rule; writers always ask |

Rules for the standing forms, all CODE-derived:
- Policy rules never exceed L1 and are recorded as policy, so doctor and `approvals log` can show them. That fits: a standing consent is a pre-decision by the operator, cap included.
- The existing cap is `policy_max_uses`, a COUNT. A cost-bearing verb needs a spend cap too: **per-run** (agents, projected tokens) and **per-day** (summed from `workflow.run.*` events, once those exist). The count cap is not enough; one run can be 10x another. This is gap G1.
- The caps check the plan before launch and are re-evaluated on a relaunch after a stop, because a resume replays and may re-run agents (VERIFIED: a failure in the middle reruns completed later agents).
- A cap overrun mid-run cannot be enforced by ddflow from outside; Claude offers `p` pause and `x` stop in `/workflows` (VERIFIED), and ddflow can only tell the operator.

### 2.4 Two prompts, not one

Claude Code has its own consent surface (VERIFIED):
- Manual and acceptEdits: a prompt on every run (phases shown), with "Yes, and don't ask again for `<name>`" for saved workflows. Desktop: card with Once, Always, Deny.
- Auto: first launch only (a Yes is saved in user settings); skipped when ultracode is on. Bypass never prompts. `-p` and the SDK never prompt: launching needs a `Workflow` or `Workflow(<name>)` allow rule, auto or bypass mode, a `PreToolUse` hook returning allow, `--permission-prompt-tool` or `canUseTool`. In plan mode a hook's allow alone does not start it.

Consequence for ddflow:
- ddflow's approval is the cost consent; Claude's prompt is the host's permission. ddflow should never write `Workflow` into allow rules to "make the second prompt go away" unless the operator's consent mode is `ask_above` or `auto_readonly_below` and the rule is `Workflow(<name>)` for one saved workflow. A bare `Workflow` allow, or a ddflow `PreToolUse` hook that always allows, silently turns the Claude gate off and leaves only ddflow's.
- A `PreToolUse` hook on the `Workflow` tool name is the natural enforcement point for a consent check (VERIFIED: hooks match the tool name; UNVERIFIED: whether `tool_input` carries agent count or plan, because the tool's schema was not read). Without that, ddflow can only check at the point it asks the agent to launch, which is advisory.
- "Don't ask again" in Claude's prompt and ddflow's standing consent are independent. Doctor should report both (G3).

### 2.5 Where the consent is spent

Spend the approval at launch. A saved `ddflow-*.js` workflow (shipped by `ddflow adopt`) starts with a `ddflow program consume <ask-id>` call that refuses the run on a wrong digest or expired approval. A script edit can bypass that; the stronger check is the hook in 2.4. A bypass is visible, not impossible, like every human checkpoint in `approval.py`.

## 3. Cost estimate shown before asking

### 3.1 Inputs (from the repo's own measurements)

From [token-spend.md](token-spend.md) (MEASURED on ddflow sessions, 2026-10-21 to 23, all `claude-sonnet-5-5`):
- Weighted units: base input 1, cache read 0.1, 5-minute write 1.25, 1-hour write 2, output 5 (Anthropic's published ratios, ASSUMED to hold for this model; [agent-token-features.md](agent-token-features.md) 2.1 lists 0.05x reads on Sonnet and Opus 5.5, which would change the read term).
- 98 subagents were 93% of spend: 12,729 calls, 664M units, about 6.8M units per agent, **but those agents were long-lived** (12 agents with 300 or more calls were 67%). A workflow agent is closer to a short task, so the per-agent figure below is a model, not that average.
- 52% of subagent spend was cache rewrites after idle gaps over 5 minutes. Workflow agents get the same 5-minute TTL, including on subscription (VERIFIED), and `subagentPromptCacheTtl` is the fix (VERIFIED key; v2.1.242 or later).
- Cache sharing: a fan-out of identical model, effort, agent type, tools, schema and cwd shares a prefix, and later agents wait up to 5 s so they read the first agent's cache (VERIFIED). So the first write is paid once per distinct prefix, not per agent. ddflow's recipe should keep the agent type and cwd identical across a fan-out.

### 3.2 Formula (ESTIMATED, assumptions named)

Per agent: `units = W + C * k * 0.1 + N * 1 + O * 5`, where W is the prefix write (P * 1.25, or P * 0.1 if a sibling already cached it), C the average context in tokens, k the number of model calls, N the new input tokens per call summed, O output tokens. Run total: sum of agents, plus the coordinator.

Worked example, ASSUMED inputs (not measured for workflows): prefix P = 25K, average context C = 60K, k = 25 calls, N = 40K new tokens in total, O = 8K.
- First agent: 31K write + 150K reads + 40K + 40K = about 261K units.
- Each later agent (prefix shared): 2.5K + 150K + 40K + 40K = about 233K units.
- 18 agents (261K + 17 x 233K): about 4.2M units. At an ASSUMED $3 per million base input tokens, about $13. Not a quote: prices were not verified for this model, and subscription plans meter differently (UNVERIFIED).
- Doubling k to 50 gives about 6.9M units (411K + 17 x 382K), not 2x; k and C dominate and are unknowable before the run.

How ddflow should show it:
1. A range, never a point: low at 0.5x and high at 2x of the formula, with the assumptions listed under the number.
2. Agent count first, tokens second; exact for a static fan-out, a guess for an "until dry" loop (label which).
3. Calibration: after each run, store estimate versus actual from the `/workflows` per-agent token view (VERIFIED to show usage as the run progresses; UNVERIFIED how to read it programmatically) in `workflow.run.finished`, and apply the median ratio to the next estimate. Until then print "uncalibrated".
4. Compare against Claude's own thresholds so the person sees both: the `Large workflow` advisory at more than 25 agents or more than 1.5M projected tokens (VERIFIED; advisory only; hidden when ultracode is on), and `workflowSizeGuideline` (small under 5, medium under 10, large under 50 agents; default medium, small on Pro; advice, not a cap).
5. The cache setting as a line item: 5m versus 1h (section 5).

`ddflow/core/budget.py` is a chars / 4 estimate (CODE); it is fine for brief sizing but not for dollars. Real numbers need usage fields from the harness (ATF-usage-fields and TE-limit-aware-routing, both open per ultracode.md).

## 4. Per-run and per-day caps

| Cap | Enforced where | Strength |
|---|---|---|
| Max agents per run | Plan (digest) before launch; the runtime caps 1,000 agents per run and 16 concurrent (VERIFIED) | ddflow: pre-launch check. Mid-run: none |
| Projected tokens per run | Plan before launch | Pre-launch only |
| Agents or tokens per day | Sum over recorded runs plus the estimate for this one | Pre-launch; actuals fill in later |
| Plan-limit awareness | Claude pauses a run at a claude.ai usage limit, at most twice, only when interactive and subscription-signed in (VERIFIED) | Host behaviour, not ddflow's |
| Hard stop | `--max-budget-usd` and `--max-turns` are launch caps for `claude -p`; whether they bound a workflow is UNVERIFIED (ultracode.md 1.2) | Do not rely |

A per-day cap needs the run ledger (G1); ship per-run caps first.

## 5. The cache TTL setting

- Claude Code: `subagentPromptCacheTtl` (`5m` or `1h`) covers subagents, workflows and background requests; precedence `FORCE_PROMPT_CACHING_5M`, then `CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL`, then the key, then `ENABLE_PROMPT_CACHING_1H` (VERIFIED). `promptCacheTtl` covers the main conversation (agent-token-features.md 2.1).
- Trade-off (ESTIMATED): a 1-hour write costs 2x versus 1.25x, so it pays only when an agent idles over 5 minutes at least once per few calls. In ddflow's measured sessions the idle gaps averaged 11 minutes (token-spend.md), which is why D-TE-1 chose 1h. A workflow agent that never waits on CI, review or `ddflow_wait` gains nothing from 1h and pays 60% more per write. So the right default is per recipe: 1h for wave and gate fan-outs that call `ddflow_review` or `ddflow_ci`; 5m for pure read-only research sweeps.
- ddflow shows the setting in the plan and the doctor reports the resolved value (the environment variable can override the setting; doctor must read both). ddflow cannot set it per run; it is session-wide, so the consent plan can only say what is in force. Gap G4: a repo search finds neither key in ddflow (CODE).

## 6. Recognising the host, and what a ddflow-initiated run can use

### 6.1 Detection (what is knowable)

| Signal | Source | Strength |
|---|---|---|
| Host is Claude Code | `ddflow/harnesses/claude.toml` descriptor, hook payload shape, `session_id` | CODE; strong |
| Workflows enabled for this user | `disableWorkflows`, `enableWorkflows` (plan default differs: off by default on Pro), `CLAUDE_CODE_DISABLE_WORKFLOWS=1`, managed settings | VERIFIED keys; ddflow reads none of them today (repo search, CODE) |
| Version at least 2.1.248 (workflow-authoring), 2.1.269 (concurrency variable), 2.1.284 (ultracode as a setting) | `claude --version` | VERIFIED thresholds |
| Keyword trigger on | `workflowKeywordTriggerEnabled` (index entry VERIFIED; semantics beyond the one-line index not read) | partial |
| Ultracode on for this session | `ultracode` setting (VERIFIED), but also `/effort ultracode` per session, not persisted | file only shows the persisted part |
| Org blocks | managed `disableWorkflows`; no managed key blocks ultracode alone (VERIFIED) | strong |

Recommendation: detect by reading settings files in Claude's precedence order plus the environment, report `workflows: enabled | disabled | unknown` with the source file, and treat `unknown` as "not available" (the existing rule: a tool that could not run is `unavailable`, never passed). 
### 6.2 Who can launch

Verified in this research (workflows page, "Where the keyword works"): the keyword is an opt-in only in prompts a person types (interactive, IDE panel, Remote Control, an SDK app stamping `origin {kind:"human"}`). It does not start a run from `-p`, an unstamped SDK prompt, a scheduled prompt, or a relayed webhook or PR comment. The page adds that asking in your own words ("use a workflow") also works, and the same limits apply to where that prompt comes from. Consequences:

1. A `/loop`, schedule or ddflow-relayed message cannot say `ultracode` and get a run.
2. A ddflow-initiated run is one of: (a) a saved workflow launched by name, covered by a `Workflow(<name>)` rule, auto mode or the Desktop Always choice; (b) the operator typing the phrase after ddflow shows the plan (the operator is then the consent, and ddflow only records it); (c) the SDK or `claude -p` with a launch rule or hook, which is the unattended route and the one needing the strongest ddflow consent (T2, never auto for writers).
3. Refutation, folded into ultracode.md 1.1: it says "under ultracode it (the launch prompt) is skipped". The docs say only auto mode skips the first-launch prompt when ultracode is on; the Manual and acceptEdits rows are unchanged by it. Only the `Large workflow` warning and the concurrent-subagent limit are lifted by ultracode in general.
4. Refutation: ultracode.md 4.3 treats a `Workflow` allow rule as the way for `/loop` to work. A bare `Workflow` rule approves every workflow including ones Claude writes. For ddflow, prefer `Workflow(<name>)`.
5. Do not enable `ultracode: true` for ddflow sessions (ultracode.md question 4): it removes the Large warning and the auto-mode prompt.

## 7. Other hosts: native pattern or the `program` runtime?

Basis: the comparison table in ultracode.md section 2 and [agent-token-features.md](agent-token-features.md), both repo research from earlier passes. I did not re-verify the per-agent cells in this pass: each is UNVERIFIED unless marked, and DX-uc-research-gaps (open) is the task that closes them.

Test for "could host the pattern natively": (1) the agent can run a script or recipe that spawns N sub-sessions, (2) with a typed output the script can parse, (3) with a way to resume or skip finished steps, (4) with a launch-time cap. Missing (2) to (4) means ddflow supplies them.

| Host | Native fan-out | Typed output | Resume | Launch cap | Verdict |
|---|---|---|---|---|---|
| Codex CLI | Y, subagents, `max_concurrent_threads_per_session` (reported) | P, `exec --output-schema` (reported) | P, `exec resume` (reported) | Not found | Native for read-only fan-out via `codex exec` per item; ddflow supplies journal and cap |
| Gemini CLI | ? | headless JSON only | N | P, `max_turns`, `timeout_mins` | `program` runtime, driving `gemini` headless |
| opencode | P | N | P, persistent sessions | N | `program` runtime |
| Kimi | Y, learned orchestrator (vendor claim) | N | N | N | Do not mimic the swarm (ultracode.md 5.4); `program` runtime over `kimi` or direct API |
| Qwen Code | P, subagents, `model.maxSubagentDepth` | N | N | Y, `--max-wall-time`, `--max-tool-calls`, `--max-session-turns` (reported, agent-token-features) | `program` runtime; caps usable as launch caps |
| Copilot CLI | P, `/fleet` (reported) | N | N | P, `--max-ai-credits N` soft (reported) | `program` runtime; credit cap is useful |
| Cursor | Y, GUI, 8 agents | N | N | N | Not scriptable for ddflow; GUI-only, `unavailable` for unattended runs |

Decision rule: a host can run the pattern natively only if it takes a recipe and returns schema-valid results. By that rule no host in the table qualifies on its own except Claude Code; Codex is closest. Everything else needs the `program` runtime, whose tier T1 (direct reviewer-transport model calls, no coding agent) works on any LLM with an HTTP API, including local servers. The consent design in section 2 carries over unchanged: the gated verb is `program.launch.<runtime>`, the digest has the same fields, and only the cost formula's cache terms change per provider (agent-token-features.md 2.1 lists the ratios: Kimi k3 writes $3 or $6 per M, Gemini cached 0.1x with no TTL control in the CLI, Qwen explicit cache 1.25x write 0.1x hit).

## 8. Gaps to file (checked with `ddflow similar`, nothing at or above 0.5)

| Id | Gap |
|---|---|
| G1 | No spend-bearing gated verb and no spend cap: `policy_max_uses` counts uses, not agents or tokens; no per-run or per-day cap; no run ledger |
| G2 | `ddflow adopt` ships no consent-aware saved workflow and no `Workflow(<name>)` rule offer |
| G3 | Doctor does not report Claude Code workflow state (`disableWorkflows`, `enableWorkflows`, `workflowKeywordTriggerEnabled`, `ultracode`) or the standing "don't ask again" grants |
| G4 | Doctor does not report the resolved prompt-cache TTL (`subagentPromptCacheTtl`, `promptCacheTtl`, and the environment overrides) |
| G5 | No PreToolUse handler for the `Workflow` tool name to enforce a consent digest |
| G6 | The ultracode.md statements corrected in 6.2 items 3 and 4 (folded in; left in ultracode.md until its own task is reopened) |

## 9. Unverified, for a probe

- Whether the `Workflow` PreToolUse payload carries the script or agent count.
- `workflowKeywordTriggerEnabled` and `enableWorkflows` semantics beyond their index lines (342 KB of the settings page unread).
- Programmatic per-agent token usage; whether `--max-budget-usd` stops a workflow; every cell in section 7.
