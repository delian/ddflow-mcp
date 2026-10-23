# Operator digest: what waits on the operator, and where the project stands

Task `OD-research` (phase `P-operator-digest`). Proposal: [decisions/operator-digest.md](../decisions/operator-digest.md). Docs only; nothing here is built.

Operator request, 2026-10-23, verbatim: "is this question for the status - what waits on me driven by claude or by the ddflow? if it is not ddflow, should we think of improving the periodical info that ddflow reports to also report progress, status, potential eta and what is waiting on the user, so if the user has missed it to be able to ask, as I periodically do"

## 1. Answer to the question asked

"What waits on me" is driven by Claude today, not by ddflow. The lead compiles it by hand from agent reports. What ddflow itself knows and shows is one line at the end of `ddflow status` ("N approval request(s) waiting for the operator (`ddflow approvals list`)", `surfaces/commands/reporting.py`), and the list that line points at (`ddflow approvals list`). Everything else a person must do or decide is in nobody's view: DECIDE tasks, a gate that needs a skip, `chore approve`, an item that needs `complete --force`, a red main, a credential that expired, an agent whose run died on a rate limit. When the lead forgets, the operator has to ask ("what is the status", about 20 times on 2026-10-16, DX-status-digest evidence), and each answer costs 5-8 commands plus ad-hoc scripts. That is the defect: a state-derived fact has a model-derived delivery.

So yes: improve the periodic report. The proposal is one deterministic computation, in ddflow, with two outputs: the **operator queue** (what waits on you, ranked, aged, with the exact answer command) and the **digest** (queue on top, then progress, movement since last seen, in flight, stalled, risks, ETA).

## 2. What exists (inventory, so nothing is built twice)

| Piece | Gives | Gap for this request |
|---|---|---|
| `ddflow status` | Completed, in flight, waiting for files, `Health:` (STALLED/IDLE leases), ready, parallel width, the one approvals line | no age, no DECIDE, no ETA, not compact, not delta-aware |
| `Progress:` block after `complete` (`services/progress_line.py`, `[session].progress_after_complete`) | tasks/bugs/phases done of total, phase line, next 3 | only fires when an agent completes something; says nothing about waiting-on-you or ETA |
| `ddflow board` | generated queue markdown | whole queue, no operator view |
| `ddflow health` / `[health]` / `ddflow cadence` | IDLE/STALLED/unresponsive findings with the correcting command; `health_watch` falls due on a timer | findings, not "who must answer" |
| `ddflow metrics forecast` (`services/metrics/forecast.py`) | critical-path ETA of a phase as a bootstrap range, seeded, honest "not enough data" | per phase only; says `not enough data` on this project today (needs 10 completed per kind) |
| `ddflow metrics lanes` / lock metrics | lane utilisation, idle beside ready work, queue waits | not joined to an ETA |
| `ddflow approvals list` / `RA-brief` | pending approvals; RA-brief will put one line in brief/status | approvals only |
| `ddflow export` + `kind_worklog.py` | generated documents with `--check/--update` | no digest document |
| `ddflow hooks session-start` / prompt hook | prints the brief; always exit 0 | carries no operator queue |
| `ddflow gate list --refuted`, `gate list --overrides` (RA-audit) | forced/partial/refuted outcomes awaiting spot-check | not aggregated |
| Open tasks | `AW-inbox` (the operator list, the same idea as this research), `AW-since-cursor` (since-last-looked), `DX-status-digest` (`ddflow digest`), `AW-event-classes` + `AW-sinks` (push), `RA-brief`, `DX-waves-status`, `GR-wave-derived`, `DX-parallel-width-report`, `LB-waves` | designed in pieces, none built; `DX-status-digest` needs three unbuilt tasks, so nothing can ship early |
| `services/approvals/sites.py` | the gated-command table: every operator-only verb as data, with a test that fails on a new one | the natural inventory of "needs a person", reusable |

Conclusion: roughly 70% of the data is already computed by something. The missing pieces are a single collector of operator-owned entries, age/impact/ranking, the "last seen" memory, and one place that composes them. AW-inbox, DX-status-digest and AW-since-cursor already describe the same product in three slices; P-operator-digest is the one coherent route to them (section 9 proposes folds).

## 3. The operator queue (OQ)

An entry is a pure record built from state; the text of every field comes from templates and log fields, never from agent prose when a field exists, and `unavailable` is never worded as success.

```
id         stable: <source>:<subject>  (e.g. approval:ask-17, decide:GP-decide, red-main:ec768ee)
what       one line, from a template
why        the fact that makes it a person's job (the refusal, the policy, the failure text)
since      event timestamp it became true -> age at render
default    the recommended answer (DECIDE task body "default", proposal table, or the single safe action)
answer     exact command, plus the chat route (see 6)
blocks     who is blocked (agents/items) and how many items transitively (impact, from the item graph)
severity   info < nudge < needs < urgent
```

Sources (each is a small pure function over `State`, `Config`, `now`; the table is data like `approvals/sites.py`, with a test that fails when a source kind is not registered):

| Source | Detected from | Answer command | Blocks |
|---|---|---|---|
| pending approval request | ask records, `approvals list` | `ddflow approvals approve <id>` / `deny` | requester, its item |
| DECIDE task | kind `doc` whose title starts `DECIDE` and state open and needs done (e.g. GP-decide, RA-decide before it ran) | lead asks via AskUserQuestion; the questions and defaults are in the task's doc | every item whose `needs` reach it (impact count) |
| gate needing skip / extra review round | a gate at the round cap or `unavailable` with no alternative; the printed approval id | `ddflow approvals approve <id>` | the item |
| completion needing `--force` | item whose pipeline is complete except a refused gate; hook refuses agent `--force` | the approval route (RA tier T2) | the item and dependants |
| `chore approve` | pending chore proposals | `ddflow chore approve <id>` | none usually |
| unverified/unavailable/partial gates awaiting spot-check | `gate list --refuted`, `--overrides` | `ddflow gate list --refuted` then accept or reopen | trust, not flow |
| proposed or stale decisions | decisions in `proposed`, or `accepted` but unconfirmed (QD docs: "accepted by default, UNCONFIRMED") | `ddflow decision show <id>` then accept/supersede | items under its globs |
| red main | latest `ci.recorded` on main failed, or the red-main bug open | the fix task id | every merge (impact = ready queue) |
| stalled / idle / dead lease | `health` findings above N minutes; lease expired | `ddflow health` correcting command (nudge) | waiters on its files |
| agent run failed | rate limit / spend limit text in the run record, or the lease expired with no heartbeat | restart the agent; raise the limit | its item |
| waiting for credentials | `unavailable` reviewer whose cause is a missing key (kimi key) | where the key goes (never the key) | the gate |
| unanswered agent question | `session note` / ask of kind question with no answer | `ddflow approvals ...` or reply | the asker |
| missed `phase close` | phase with every task done and `ready to close` | `ddflow complete <phase>` (an agent can; shown as "lead", not the operator) | none |

Entries the lead can answer alone are tagged `owner: lead`; the queue shown to the operator by default is `owner: operator` only, `--all` shows both. This keeps the signal clean (the operator was complaining about missing items, not about noise).

**Ranking and resurfacing.** Order: severity, then impact (items blocked), then age. Severity rises with age by a table (knob `[digest].escalate_after`, default `1h, 24h`, and `escalate_after_red_main` default `6h` for urgent: `nudge` -> `needs` after the first, `urgent` after the second). A row carries its age ("waiting 2d 3h"). When the operator last looked (OD-seen) before the entry appeared, it is marked NEW; when the operator saw it and it is still open, it is marked `still waiting (seen 6h ago)`, and the age keeps counting from the first appearance, never from the last look. An entry never disappears by being displayed.

## 4. The digest

Deterministic text from `(State, Config, now, seen cursor)`. Sections in order, each omitted when empty except the first two:

1. **Waiting on you** (the OQ, count + top N rows; `--all` for the rest).
2. **Progress**: tasks, bugs and phases done of total (the existing `_progress` computation, reused), per open phase `done/total` with a bar, only phases with movement or in flight unless `--phases all`.
3. **Since you last looked** (OD-seen; or `--since 3h`): merges (count and ids), completions, new/closed bugs, gates that failed or went unavailable (listed as such: forced, partial, unavailable), phases closed.
4. **In flight**: per agent, item, how long, last progress age, what it waits for (the lease-wait view).
5. **Stalled / risks**: STALLED/IDLE (reuse `health`), red main, lock queue waits above threshold, the oldest open approval, quota pauses, a phase whose ready width is 0 (nothing can be taken: say why), docs-hold effect if present.
6. **ETA** (section 5).
7. **Next**: the three the scheduler would offer.

`--json` is the same data; text is rendered from it. The operator and agents see identical data. Same state and same `now` give byte-identical text (sorted keys, no set iteration, injected clock, seeded sampling), so tests assert on exact output and a webhook can dedupe by digest hash.

## 5. ETA: method, confidence, honesty

An ETA that looks precise and is wrong is worse than none, so the rules are fixed:

- Always a **range** and the word **estimate**, with the method named: `estimate 2d 4h - 5d (critical path, bootstrap of 31 completed items, 80% interval)`.
- **Method 1, per phase critical path**: reuse `metrics forecast` (longest unfinished chain; each link drawn from observed durations of its kind; link in flight less elapsed). This is a floor with unlimited agents.
- **Method 2, throughput**: items completed per day over a window (`[digest].eta_window`, default 7d) against remaining count, divided per phase; reported as the other end of the range. Remaining count excludes abandoned and the docs-hold items only when the hold is stated.
- **Method 3, queue wait** (adds to Method 1): median lock/queue wait from lock metrics (`metrics lanes`), applied per link on the contended globs. Shown as a separate term "+ waiting for files ~X".
- Range = [max(method 1, ...)...] low from method 1, high from method 2 + queue wait; if the two disagree by more than 3x the line says `low confidence` and why.
- Confidence labels from sample size: fewer than `min_samples` (10 per kind, the forecast's own guard) -> `unavailable: not enough completed items of kind X (have 4, need 10)`. **Unavailable is shown, never 0, never "done soon".**
- Whole-queue ETA is not computed (the docs hold makes 37-wave critical path a statement about order decisions, queue-review 2026-10-23); ETA is per **phase** and for named **watched** phases (`[digest].watch = ["P-remote-approvals"]`), plus the **critical path wave count** from `GR-wave-derived`/`DX-parallel-width-report` when built (shown as waves, not time).
- Every ETA states what it ignores: rework, review rounds beyond the median, operator answers pending (an ETA with open operator entries blocking the path says `+ your answers`: the ETA is conditional on the queue on top).

## 6. How the operator is told (delivery)

| Channel | Content | Budget / cadence |
|---|---|---|
| `ddflow digest` (CLI) | full digest, `--since`, `--all`, `--json`, `--ask` (the OQ as the exact AskUserQuestion payload) | on request |
| MCP | an action of an existing family (section 8) | on request |
| `brief` / SessionStart | a compact line: `Waiting on you: 3 (oldest 2d: GP-decide). ddflow digest` plus the top 3 titles, counts only otherwise; ETA never | within the effective session budget (D-session-start-budget: SessionStart carries at most one dynamic line; resume > approvals > upgrade today, this becomes "> operator queue") |
| `Progress:` block after `complete` | one extra line: `Waiting on you: N (oldest ..)` and the phase ETA range | the block stays 3-5 lines |
| generated `docs/ddflow/DIGEST.md` via `ddflow export digest` | full digest, regenerated on `--update`, `--check` in CI | on change of the queue or on cadence; the file is what a remote operator reads on a phone via the forge |
| notification sinks (AW-sinks) | new queue entry, red main, spend-limit failure, and a periodic digest (e.g. daily 09:00) | on change and on schedule through `ddflow cadence` (`digest_push` falls due like `health_watch`) |
| chat / `AskUserQuestion` | the exact entries | rule below |

**The rule for agents and the lead** (put in the prompt, OD-prompt, and checked by a test of the prompt text): when the operator asks for status, or a session starts, the agent runs `ddflow digest` and relays the "Waiting on you" section verbatim, not a paraphrase; when it needs a decision it puts the queue entry (what, why, default, command) in front of the operator by `AskUserQuestion` (or the approvals channel), generated by `ddflow digest --ask`. This closes the gap this request came from: the model no longer decides whether to mention something; it relays a computed list.

The only part ddflow cannot compel is that a human reads a terminal. Hence the sinks (C) and the generated file; the cursor makes a missed item resurface.

## 7. Since last seen (OD-seen)

`seen` is `.ddflow/local/digest_seen.json` (git-ignored), keyed by operator identity (`[identity]`, the person's login), value: `{ts, digest_hash, entry_ids}`. It is set by `ddflow digest --mark-seen`, and implicitly when the digest is printed to a human terminal (not by an agent: an agent printing it cannot mark the operator as having seen it; `is_agent` is the existing test, RA-inventory). The cursor is local state, not an event: replay does not need it, and losing it only makes the next digest longer. Two operators on one project have two cursors.

## 8. MCP, config, layering

No new tool (D-mcp-new-nouns, D-mcp-tool-diet): `digest` is declared in the matrix with a **family and action**. Candidate placement: action `digest` of the status/overview family (the family that already holds `status`, `board`, `progress`); the answering verbs stay where they are (`ddflow_approvals`/session ask family, RA-mcp). The collector is a service, the CLI and MCP only parse and render (CLI/MCP parity from the declared matrix). Fallback if MTD-family-route lands another way: the `ddflow_record` family with kind `digest` (read-only). OD-mcp decides by reading the matrix then; neither adds to the 24 advertised tools.

Knobs under `[digest]`, each settable by file, CLI, MCP and prompt (D-everything-configurable), layered by D-config-layer-order: `top_n` (3), `escalate_after`, `escalate_after_red_main`, `stale_after`, `stall_minutes` (shared with `[health]`, not a second quantity), `eta_window`, `eta_method`, `watch`, `push_on` (list of change classes), `push_every`, `min_samples`, `include_lead_owned` (false). Company floors in the top layer are **tighten-only**: a company may set `escalate_after` shorter, require `push_on` to include `red_main`, forbid turning the operator queue off in the session start, never lengthen. A user may lower `top_n` but not below 1 when `required`.

## 9. Fold, link, do not duplicate

| Existing | Proposal | Reason |
|---|---|---|
| AW-inbox | OD-queue **is** AW-inbox's collector. Propose `ddflow task fold AW-inbox into OD-queue` (the `inbox` verb stays a CLI alias of `digest --queue`) | same list, same fields; OD adds age, impact, ranking |
| DX-status-digest | OD-digest takes the `digest` verb. Propose folding it into OD-digest/OD-cli | it needs DX-tools-list-budget-headroom, DX-waves-status, DX-registry-files-shared (unbuilt) and so cannot ship early; its sections are kept, waves section arrives when DX-waves-status/GR-wave-derived land (soft dependency, not a need) |
| AW-since-cursor | OD-seen takes it | same cursor |
| AW-sinks, AW-event-classes | **not folded**; OD-sinks needs them and adds two classes (`operator_queue.new`, `digest.periodic`) | another phase's engine, one sink interface |
| RA-brief | OD-brief **needs** RA-brief: one budgeted line for approvals and queue, not two lines | one dynamic line in SessionStart |
| `progress_line.py` | extended, not replaced | the Progress: block is the existing periodic report |
| `metrics forecast` | reused as method 1 | already seeded and deterministic |
| DX-waves-status / GR-wave-derived / DX-parallel-width-report | consumed when present (soft) | critical path wave counts |

Folding is the lead's call (and only for items nobody holds); this proposal does not run any fold. OD-decide asks the operator.

## 10. Options

| | A: extend `ddflow status` only | B: `ddflow digest` view + brief hook | C: B + sinks/push + generated doc |
|---|---|---|---|
| What | add queue rows, ages and ETA to status | new view, status keeps its one line + "see digest"; compact line in brief/SessionStart/Progress | B plus periodic/on-change push and `DIGEST.md` |
| Fixes "I must ask" | no: still pull-only and long | partially: every session starts with the queue; operator in an active session sees it | yes: a missed item reaches the operator |
| Token cost | status grows (already ~50 lines) | counts + top 3 in brief, rest on request | same as B; push messages are rate-limited |
| Build | small | medium (collector + render + hooks) | B + sinks (AW-sinks is a prerequisite and not built) |
| Risk | status loses its "what is happening now" focus; ETA noise | duplication with inbox/DX-digest unless folded | sinks leak or spam; quiet hours/rate limit/redaction needed |

**Recommendation: C, delivered as B first.** Rollout: (1) OD-inventory, OD-queue, OD-cli: the queue as `ddflow digest --queue`, no new dependencies (the first usable slice); (2) OD-digest (progress, in-flight, risks, ETA), OD-seen; (3) OD-brief (budgeted line) once RA-brief lands; (4) OD-export, OD-mcp, OD-prompt; (5) OD-sinks when AW-sinks lands; (6) OD-e2e, OD-docs. Status keeps its single line, now `N waiting for you (oldest ..): ddflow digest`, replacing the approvals-only line. Options are cumulative, so stopping after step 3 is B and still a complete answer to "driven by claude or ddflow".

**What would invalidate it.**
- If the operator says the lead's hand-compiled list is wanted as prose, the queue is still the data under it; only the delivery rule (section 6) changes.
- If most waiting entries turn out to be lead-owned (so the operator queue is nearly empty), the push channel (C) buys little: measure with the first slice for a week (`include_lead_owned` counts).
- If the always-on budget makes even one extra SessionStart line unacceptable for a small-window agent type, brief carries nothing and the rule in section 6 (run `ddflow digest` first) carries the weight.
- If ETA samples never reach `min_samples` for a kind, ETA stays `unavailable` and only progress and queue ship; it must not be replaced by a guess.
- If AW-sinks is re-scoped, C waits; B is unaffected.
- If a source detector (e.g. "red main") proves unreliable, an entry that is wrong is worse than one missing: each detector ships with a replay test over the real log (fold the last week, assert counts are sane), as AW-event-classes asks.

## 11. Open questions (defaults in the proposal doc)

Recorded as Q1-Q9 in the decision proposal; OD-decide asks them.

## 12. Defects and gaps met while researching

- `ls` is aliased to `exa` which is not installed in this environment, so every `ls` in a shell fails with "exa: command not found" (environment, not ddflow).
- `ddflow list` with no kind exits with an argparse error although `status` suggests `list` as the generic verb (usability only).
- `ddflow task add` prints a "possible duplicate" list for the new phase before success; harmless, noted.
- `ddflow metrics forecast` reports "not enough data: completed items per kind (phase: 0)" on a project with 1334 completed tasks. The message names the kind "phase" with 0 samples; whether the forecast should draw from task spans for a phase's chain was not investigated here. Either way ETA per phase may be `unavailable` today; OD-digest therefore has the throughput method (2) as the fallback, and OD-digest's first step is to check this against the real log.
- The `TMPDIR` variable is unset in the agent shell, so `$TMPDIR/x` becomes `/x`; scratch paths must be explicit.
