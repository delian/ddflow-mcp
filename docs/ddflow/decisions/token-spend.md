# Token economy: decision proposals

Status: **DECIDED** 2026-10-23 (operator answered TE-Q1..TE-Q8; each answer is a `D-TE-Qn` record; see Answers).
Evidence: `docs/ddflow/research/token-spend.md` (section numbers refer to it). Phase
`P-token-economy` (prefix TE-). Related phases: `P-lean-start` (start cost), `P-operator-digest`
(operator-side digest and queue), `P-batch-gates` (review and CI batching), `P-tool-diet` (tool list
size), `P-soft-upgrade` (cache-friendly upgrades), task `B-gap-spend` (usage ledger).

Operator request (2026-10-23, verbatim): "In parallel - make a research on how the session tokens
are spend for the development of the ddflow with claude. I see the context is 43.7% messages, and
I wonder is there a way to optimize this and to save on tokens somehow? Maybe make the messages more
compact, or something like this? But I might be wrong, so make a research and give me report and
proposals".

Headline (MEASURED, one lead session plus its 98 subagents, 2.5 days): the lead is 7% of the
weighted spend, the subagents 93%; 52% of the subagent spend is whole-context cache re-writes after
waits longer than the 5-minute subagent cache TTL. "Messages 43.7%" is a symptom of the lead's long
context (worth a compaction rule, about 3% of the total), not the main lever.

## Proposals

State legend: PROPOSED (no decision), DECIDED (answered, tasks follow), PLANNED (task filed, waits for TE-decide), DONE, REJECTED.
Saving is the ESTIMATED share of total weighted spend (research section 7).

| Id | Proposal | Task | Saving | Effort | State |
|---|---|---|---|---|---|
| D-TE-1 | Subagent cache TTL 1 hour: `CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL=1h` (or `subagentPromptCacheTtl`); `doctor` warns when unset; setup and launch templates emit it | TE-subagent-ttl | 40-48% | tiny | DECIDED |
| D-TE-2 | Context caps: lanes rotate at 200-300 K with a `ddflow brief` hand-off; `CLAUDE_CODE_AUTO_COMPACT_WINDOW` for agents and lead; lead compacts at a task boundary below 400 K | TE-context-caps | +20-30% on D-TE-1; lead 3% | small | DECIDED |
| D-TE-3 | Poll cadence: waits and polls return within 240 s so a 5-minute cache stays warm; fallback for hosts that cannot use the 1-hour TTL | TE-poll-cadence | 30-37% alone, ~0 with D-TE-1 | small | DECIDED |
| D-TE-4 | Model routing per role, measured first (A/B of rounds per item and rework), strongest model for design and every reviewer, cross-family review unchanged | TE-model-routing | 0-30% of fix-lane spend, unmeasured | medium | DECIDED (amended: route by headroom now) |
| D-TE-5 | Review economy: reviewer prompt contract (no repeated closed findings, delta rounds), stop rule at round 4 | TE-review-economy | 2-4% | medium | DECIDED |
| D-TE-6 | Bounded outputs: default caps and a "N more, run X" footer on show/list/status/gate status/review; `--brief` | TE-bounded-outputs | 1-3% | medium | DECIDED |
| D-TE-7 | Compact report protocol: fixed template (verdict, ids, at most 6 lines, pointer), details by reference; wake-ups answer with the delta only | TE-report-protocol | 0.5-1% | small | DECIDED |
| D-TE-8 | Spend-limit stop and resume: agents stop with a one-line state on the limit message; one broadcast resumes all (uses B-quota-pause) | TE-spend-resume | 1% | small | DECIDED |
| D-TE-9 | Cache hygiene: stable tool list and system prompt in a session, tool search kept on, `idleCompaction` on, `Prompt cache` visible in the status line | TE-cache-hygiene | 0.5-1% | tiny | DECIDED |
| D-TE-10 | Worker start budget: verify with the measurement that P-lean-start brings worker starts under 32 K; no new code unless it does not | TE-worker-profile | 2-5% (owned by P-lean-start) | none here | PLANNED (link) |
| D-TE-11 | Interim transcript report reproducing the research aggregates, later re-pointed to B-am-ingest / B-gap-spend | TE-measure | 0 (enabler) | small | DECIDED |
| D-TE-12 | Brief and rules dedupe | TE-brief-dedupe | under 0.1% | medium | PROPOSED, low priority: only if the post-change measurement shows the brief above 3% |
| D-TE-13 | Lead habits: shorter replies, no repeated tables, bounded commands, no re-listing; written into the driver deltas | in TE-report-protocol | 0.3-0.5% | none | DECIDED |
| D-TE-14 | Re-measure after the changes and compare with this report | TE-verify | 0 (proof) | small | DECIDED |

Rejected or not proposed (evidence in the research): a smaller CLAUDE.md or SessionStart payload
(2.5 KB and 3.7 K tokens once per session); capping agent parallelism by spend (parallelism does not
change per-agent spend in this data; the limit is reached because of the 5-minute rewrites, not the
head count); cutting review gates (rework is 2%, the gates pay; D-TE-5 cuts wasted rounds instead).

## Questions for the operator (task TE-decide), each with a default

- **TE-Q1** Set the subagent cache TTL to one hour for every Claude Code agent that ddflow launches
  (env block of the launcher and the project settings)? Default: **yes**; a 1-hour write costs 2x
  instead of 1.25x, the model says the net is about -50% of subagent spend. Requires Claude Code
  2.1.242 or later; the plan limit may weigh writes differently, so TE-verify checks it on the real
  limit meter.
- **TE-Q2** Context cap for lane agents and the lead? Default: **rotate lane agents at 250 K, lead
  compacts at a task boundary below 350 K**, with the hand-off note. Alternative: auto-compact only
  (`CLAUDE_CODE_AUTO_COMPACT_WINDOW=300000`), keeps the summary but loses ordering detail.
- **TE-Q3** Poll cadence while waiting? Default: **a single wait call never outlives 240 s** (the
  agent re-calls it); heartbeats ride on that call. Only needed when Q1 is declined or the host
  bills usage credits.
- **TE-Q4** Model routing? Default: **do not change models yet**; run an A/B on 20 mechanical fix items
  with a cheaper model first and decide on rounds and rework. Reviewers and design work stay on the
  strongest model; the cross-family reviewer rule is unchanged.
- **TE-Q5** The measurement tool: interim script now, or wait for B-am-ingest? Default: **interim,
  read-only, redacted aggregates only (no transcript text), explicitly replaced by the B-am-ingest
  reader when it lands** (it must not become a second transcript reader).
- **TE-Q6** Subagent report format? Default: **at most 6 lines plus a pointer**, the detail in the
  item's evidence or a file under the agent's scratch; repeat wake-ups answer the delta only.
- **TE-Q7** Output caps? Default: **on for agents (`--brief` default when `DDFLOW_AGENT` is set), off
  for the operator's terminal**, with a footer naming the unbounded command.
- **TE-Q8** After a spend-limit stop, resume automatically on a timer or wait for the operator?
  Default: **wait for the operator** (money decision), but resume everything with one command.

## Answers

DECIDED 2026-10-23 by the operator (verbatim words are in the session log and in each decision).

| Q | Answer | Record | Tasks |
|---|---|---|---|
| TE-Q1 | Yes: 1-hour subagent cache TTL for every Claude Code agent ddflow launches | D-TE-Q1 | TE-subagent-ttl |
| TE-Q2 | Defaults (lanes rotate at 250K, lead compacts at a task boundary below 350K, hand-off note) PLUS a user-selectable auto-compact window (Claude Code `CLAUDE_CODE_AUTO_COMPACT_WINDOW`, `autoCompactWindow`, `--autocompact`, `/autocompact`; verified in the docs) and context-window extension when an agent cannot finish before compaction (decided at launch or rotation: a running session usually cannot change its window; warn the user) | D-TE-Q2 | TE-context-caps (amended), NEW TE-compact-window-per-agent, NEW CC-window-extend |
| TE-Q3 | Yes: a single wait call never outlives 240 s, always (not only as fallback) | D-TE-Q3 | TE-poll-cadence (amended) |
| TE-Q4 | Route now to cheaper models or models/subscriptions with spending headroom; keep the A/B as the quality check; reviewers and design stay strongest satisfying the cross-family rule | D-TE-Q4 | NEW TE-limit-aware-routing, TE-model-routing (amended) |
| TE-Q5 | Interim script, replaced by the B-am-ingest reader | D-TE-Q5 | TE-measure |
| TE-Q6 | At most 6 lines plus a pointer | D-TE-Q6 | TE-report-protocol |
| TE-Q7 | Bounded `--brief` by default for agent shells, terminal unbounded | D-TE-Q7 | TE-bounded-outputs |
| TE-Q8 | Check the limit reset automatically and RESUME automatically (not wait for the operator); capped by `[spend].auto_resume`, max resumes per day, quiet; reported in the digest | D-TE-Q8 | NEW TE-auto-resume, TE-spend-resume (amended) |

Wave convention for the tasks named in the Tasks column above: `wave:N` tags follow D-labels and
D-wave-semantics (derived, priority-ordered, never hand-edited). The stamp writer is not a
CLI yet: `ddflow waves` does not exist today — `ddflow waves --stamp` arrives with LB-waves,
and GR-wave-derived makes the tags derived values. Until then the followable mechanism is
the roadmap-review restamp script `docs/ddflow/research/scripts/wave_stamp_accurate.py`
(via `ddflow update --add-tags`/`--remove-tags`; `docs/ddflow/research/queue-review-2026-10-23.md`),
which stamped every open task of the queue on 2026-10-23 except the 14 items in flight (not
touched; the TE-* follow-ups filed after the review carried none until the next --apply).
The gap is filed as bug B47acb25115.

Recorded tension (TE-Q8): a money decision the operator delegated; the proposal default was to wait. Bounded by the daily cap and the digest report.
Honest limit (TE-Q2): window extension is a launch-time or rotation-time decision, a recommendation for the lead; larger windows cost more per call and per cache rewrite; ceiling knob, company floors tighten-only.
