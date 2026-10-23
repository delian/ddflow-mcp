# MCP tool diet: fewer tools, per-client profiles, next-step hints

Research item RM-tool-diet-research, 2026-10-19. Every number below was measured on this tree
(ddflow 0.2.4, `tools/list` built by the real `Server`, `[mcp].tools = all`) unless marked
CLAIMED. Evidence labels: CONFIRMED = read in the primary source or measured here; CLAIMED =
secondary source or vendor statement not reproduced; THEORETICAL = reasoning, not measured.

## Recommendation (decision-ready)

1. Make the default advertised surface **24 tools / ~53 KB** (was 123 tools / 104 KB): 9 hot
   singles kept as they are (`identify brief next claim heartbeat complete merge show help`) plus
   15 **noun families with an `action` enum** whose names and actions equal the CLI's `noun verb`
   (`ddflow_gate action=record`, `ddflow_bug action=found`). The declared-command matrix stays
   the single source; families are generated from it, not hand-written.
2. Keep every old name **callable** as an alias (the `registry.Alias` machinery and the once
   per session deprecation note already exist) through one deprecation window; aliases are not
   advertised.
3. Per-client profiles (P-agent-parity.tool-profile) choose between: `lean` (15 tools, ~29 KB, for a
   cap near 40), `families` (24, ~53 KB, the default and the 64 profile), `hot+families` (36, ~62 KB,
   for 100/128 clients that like direct names) and the legacy flat `all`/`standard`/`core` (opt-in
   only). Profile choice comes from the descriptor cap (+ `initialize.clientInfo.name`), never from
   a runtime call: `listChanged` stays false.
4. Do NOT build a second dispatcher: the single-tool escape hatch for the smallest caps is the
   already planned PM-search-call facade (`ddflow_search`/`ddflow_call`, reached via CP-mcp-profiles).
5. Add an optional `next` field to state-changing results, hard-capped at 3 hints / 400 bytes.
6. A caps budget must count OTHER servers: a client cap (128, 100, 64, 40) is shared by all MCP
   servers, so ddflow should not take the whole of it; the per-profile targets in section 4 (15 for 40, 24 for 64, 36 for 128) are the practical ceiling, between a quarter and over a third of the cap, because fewer would split families too far. Today
   `all` (123) already leaves only 5 slots under 128 and is over Devin/Windsurf 100 by 23.

This amends D-mcp-surface (1) "no tool is renamed or merged": that line was written when merging was
estimated to save ~5% (it did not count the per-tool envelope and the 123-tool count). It needs a
new operator decision before the work starts (follow-up task 1).

## 1. Inventory (measured)

| tier | tools | compact `tools/list` bytes |
|---|---|---|
| core | 32 | 34,702 |
| standard (core + standard) | 81 | 71,745 |
| all | 123 | 104,151 |

(brief said 119 tools; the registry now has 123. tools/list is ~104 KB, above the 100,000 report line.)

- 603 parameters in all; median 4 per tool; 17 tools have 10 or more. `as_agent` is on 122 tools.
- Largest schemas (bytes): review 2185, task_add 2140, update 2118, lesson_add 2096,
  decision_add 2029, research_add 1791, phase_add 1709, workflow_gate 1638, upgrade 1552, gate_record 1514.
- ~13 KB of text would describe all 123 tools as a one-line catalog with parameter names (what a
  `ddflow_help` catalog or a search result costs when paid on demand instead of up front).
- Full per-tool table (name, bytes, params, tier, calls) is the mapping table in section 5.

## 2. Usage (measured, counts only)

Source: 741 Claude Code transcript files under `~/.claude/projects/-home-delian-src-ddflow*`;
tool_use names `mcp__ddflow__ddflow_*`. No ddflow-side MCP call log exists (`.ddflow/` keeps events,
not tool calls), so this is Claude Code on this repository only.

- **616 MCP calls, 38 distinct tools; 85 of 123 tools were never called (69%).**
- 15 tools make 80% of the calls; 27 make 95%. Top: gate_record 104, claim 64, identify 63,
  brief 32, bug_found 30, gate_status 28, task_add 25, session_prompt 23, show 23, merge 22,
  bug_fixed 21, review 20, complete 19, recall 16, research_add 15.
- The same transcripts show ~47,000 `ddflow <verb>` shell invocations (rough regex, 722 distinct
  first words, so treat as order of magnitude): `gate` ~10.4k, `review` ~5.5k, `tests` ~4.1k, `show` ~3.9k,
  `heartbeat` ~2.4k. **Agents on this repository use the CLI about 75x more than MCP**; the stale
  0.2.1 MCP server and the hook that points at the CLI are part of why. So the MCP surface that
  matters is the one for clients that have no shell (IDE chat modes), where there is no usage data yet.
- Caveat: a long tail that is never called by Claude Code here may be used by other clients;
  the tail is still a cost for everyone because the whole list is paid up front.

## 3. Client limits and tool-count evidence

| Client | Limit and behaviour | Label |
|---|---|---|
| VS Code / Copilot | Max 128 enabled tools per chat request; above it the request errors `Cannot have more than 128 tools per request`. Remedies: deselect in the tools picker (Configure Tools), tool sets for reuse, or `github.copilot.chat.virtualTools.threshold` (virtual tools group large sets automatically). Custom agents/chat modes carry an allowed-tools list. | CONFIRMED (VS Code docs "Use tools with agents") |
| Cursor | Warns above 40 MCP tools: "Too many tools can degrade performance, and some models may not respect more than 40 tools". Forum reports say newer versions with dynamic context discovery tolerate 80+ without the warning. | CLAIMED (forum and secondary pages; not read in Cursor docs) |
| Windsurf / Cascade | Hard cap 100 tools across all servers, and about 20 tool calls per prompt; extra tools are truncated from the visible list. | CLAIMED (third-party setup guides) |
| Claude Code | MCP tool search is on by default on compatible models (names only up front, schemas fetched on demand); can be disabled (`ENABLE_TOOL_SEARCH=false`); supports `list_changed`; tool results warn at 10,000 tokens, default cap 25,000 (`MAX_MCP_OUTPUT_TOKENS`), 50,000 characters then saved to a file. No stated tool-count cap. | CONFIRMED (code.claude.com MCP docs) |
| Codex CLI | No stated maximum; per-server `enabled_tools` allow list and `disabled_tools` deny list; `tool_timeout_sec` default 60. | CONFIRMED (Codex MCP docs) |
| Gemini CLI | No stated maximum; `includeTools`/`excludeTools` per server; tools get FQN `mcp_{server}_{tool}`; names over 63 characters are truncated with `...`; server names must not contain `_`. | CONFIRMED (gemini-cli docs) |
| opencode | No count limit stated; warns that MCP servers add to context; tools are prefixed with the server name and can be switched off by glob, globally or per agent. | CONFIRMED (opencode docs) |
| Kilo | Not read (task forbids touching `~/.config/kilo`); assume opencode-like. | UNVERIFIED |
| OpenAI API | "Aim for fewer than 20 functions available at the start of a turn", a soft suggestion; defer rarely used tools with tool search `defer_loading`. | CONFIRMED (function-calling guide) |

Consequence for names: long qualified names matter. Gemini truncates at 63 characters and Grok
wraps names (P-agent-parity.tool-profile), and `mcp__ddflow__ddflow_` already spends 20 characters in
Claude Code. Family names such as `ddflow_gate` shorten every call.

Accuracy versus tool count:

- Anthropic, advanced tool use: with the tool search tool, MCP evaluation accuracy rose from 49% to 74%
  (Opus 4) and from 79.5% to 88.1% (Opus 4.5); a five-server setup went from ~55K tokens up front to
  ~8.7K. Recommended when definitions exceed 10K tokens or there are 10+ tools. CONFIRMED (read; the
  numbers are the vendor's own evaluation).
- RAG-MCP (arXiv 2505.03275): tool-selection accuracy 13.62% baseline against 43.13% when only
  retrieved tools are exposed, prompt tokens down more than 50%, in an MCP stress test. CONFIRMED
  (abstract); the tool counts are in the paper body, not read.
- Berkeley Function Calling Leaderboard: the "multiple function" category offers 2 to 4 candidates, and
  the blog post has no accuracy-versus-catalog-size result. A secondary article claims BFCL shows degradation
  with catalog size; that is NOT supported by the primary page. REFUTED as a citation for our case.
- Academic "tool overload" results (retrieval from 100 to 500 primitives degrading some configurations)
  came back via a search summary only. CLAIMED.
- Read together: degradation with tool count is real but has been measured against large catalogs
  (dozens to hundreds) and mostly for retrieval; there is no published measurement for 24 versus 123 tools,
  and none for action-enum tools versus flat tools. The claim "fewer, wider tools select better" is
  THEORETICAL for ddflow; the claim "union params add errors" is THEORETICAL too. Measure it (task 6).

## 4. Designs compared

Union-parameter cost is computed from the real schemas: a family's properties are the union of its
members' (largest definition per name) plus an `action` enum, and a short description. Bytes are a
lower bound (member descriptions are cut to 60 characters; expect +10%).

| Design | Tools | tools/list bytes | Wrong-call risk | Migration cost |
|---|---|---|---|---|
| Today (flat `all`) | 123 | 104,151 | name confusion among 123 (gate_* 6, rule_* 7, ...) | none |
| A1 families + 9 hot singles (recommended) | 24 | 53,312 | action/param mismatch; max 42 params on `task`/`admin` (before pruning) | aliases for 114 names |
| A2 families + 21 hot singles | 36 | 61,521 | lower: the most-called verbs keep direct names | aliases for 102 names |
| Lean profile (hot + gate, bug, session, task, view, memory) | 15 | 29,404 | needs the rest by alias or search | client caps near 40 |
| B single `ddflow` + `ddflow_help` | 2 | ~1.1 KB list; ~13 KB catalog on demand | highest: untyped `args`, client sees no schema until it asks | high (all prompts, docs, golden tests) |
| C per-client profile only (existing tiers: core 32 / standard 81 / all 123) | 32 to 123 | 34.7 KB to 104 KB | unchanged | low, and already built |

- A1 halves the list (-50.7 KB, about 12,700 tokens at 4 bytes per token, once per session and again after
  every compaction on clients that reload tools). Union params cost: the 15 families total about 96 KB (95,742 bytes) of member
  schemas folded into ~53 KB; the wasted-parameter overhead per call is the difference between a
  family's parameter count and the action's own (e.g. `task` 42 against 3 to 17 per action). Prune by sharing
  `as_agent` (122 tools carry it today; once per family is 1) and moving seldom-used flags into one
  `options` object.
- B is the cheapest list but loses typed schemas for the model, the main reason it picks the right
  arguments; PM-search-call (search then call, schemas returned on demand) is B done safely and is already queued.
- C is necessary, not sufficient: `standard` (81) does not fit a 64 cap, and `core` (32) leaves no room for
  the rest of the user's servers on a 40 cap once other servers are counted.
- Dynamic `tools/list_changed` tiers: Claude Code supports it (CONFIRMED), other clients not verified; and the
  current server answers `listChanged:false` on purpose. Not recommended as the primary mechanism: use it only
  as an optional widen-on-demand for clients that declare support (THEORETICAL).
- Deferred loading (Claude Code tool search, OpenAI `defer_loading`): free on those clients; tool names and
  one-line descriptions still matter, so family descriptions must list their actions.

### Target counts per profile

Counts are for ddflow alone; leave room for other servers.

| Client cap | Profile | ddflow tools | bytes (approx) |
|---|---|---|---|
| ~40 (Cursor warning) | `lean` | 15 | 29 KB |
| 64 | `families` | 24 | 53 KB |
| 100 (Windsurf/Devin) | `families` (or A2 36 when alone) | 24 to 36 | 53 to 62 KB |
| 128 (VS Code) | `hot+families` | 36 | 62 KB |
| no cap, tool search | `families` | 24 | 53 KB (names loaded, schemas deferred) |

## 5. Mapping table (old tool to new tool and action, design A1)

Generated from the inventory by script (families in section 4: gate, bug, decision, memory, rule,
session, job, ship, workflow, reviewers, task, lease, view, doctor, admin); reviewed by hand for the
points below. `(kept)` means unchanged.

Hand review notes:
- `ddflow_review` and `review_triage` landed in `reviewers` with the PR and companion tools; review is
  a hot, 13-parameter tool and is better kept single (A2 does). Likewise `gate_record`/`gate_run` stay
  reachable as `ddflow_gate action=record|run`, matching the CLI `ddflow gate record`.
- `update` (13 params) and `task_add` (17 params) are the heaviest and most often mis-shaped: in family
  `task` they should expose only the per-action subset in the action's description, not the union.
- `memory` mixes lesson, memory and recall: rename to `ddflow_memory` with `recall` as an action and keep
  `ddflow_recall` hot in A2.
- `view` already exists in spirit as `ddflow_list kind=`; `list`, `board`, `status`, `progress` should
  merge into one view tool with `kind` (B-view-mcp-list prior art).
- `admin`/`doctor` have 42 and 24 union params; split further or keep out of the `lean` profile.
- Nothing is dropped: all 123 names appear below; the middle column is the family tool, the third column is the legacy name suffix, and the real `action` enum value is the CLI verb with the family noun stripped (`gate_record` becomes `ddflow_gate action=record`, `bug_found` becomes `action=found`); MTD-aliases generates the alias table from this mapping. `ddflow_release` (the lease release) maps into `ddflow_lease`, so the promote/version family is named `ddflow_ship` to avoid colliding with the legacy name `ddflow_release`.

| old tool | new tool | legacy suffix (the action enum value is the CLI verb: strip the family noun) | calls | bytes | params | tier |
|---|---|---|---|---|---|---|
| `ddflow_abandon` | `ddflow_lease` | abandon | 0 | 1053 | 5 | standard |
| `ddflow_batch` | `ddflow_task` | batch | 0 | 758 | 3 | all |
| `ddflow_bisect` | `ddflow_doctor` | bisect | 0 | 662 | 5 | all |
| `ddflow_block` | `ddflow_lease` | block | 0 | 635 | 4 | standard |
| `ddflow_board` | `ddflow_view` | board | 0 | 322 | 2 | standard |
| `ddflow_brief` | `ddflow_brief` | (kept) | 32 | 949 | 5 | core |
| `ddflow_bug_file_tasks` | `ddflow_bug` | bug_file_tasks | 0 | 413 | 2 | all |
| `ddflow_bug_fixed` | `ddflow_bug` | bug_fixed | 21 | 1440 | 11 | core |
| `ddflow_bug_found` | `ddflow_bug` | bug_found | 30 | 1320 | 11 | core |
| `ddflow_bug_invalid` | `ddflow_bug` | bug_invalid | 0 | 902 | 5 | core |
| `ddflow_cadence` | `ddflow_view` | cadence | 7 | 552 | 3 | all |
| `ddflow_ci` | `ddflow_gate` | ci | 0 | 623 | 6 | standard |
| `ddflow_claim` | `ddflow_claim` | (kept) | 64 | 1458 | 9 | core |
| `ddflow_cleanup` | `ddflow_doctor` | cleanup | 0 | 631 | 2 | standard |
| `ddflow_companions` | `ddflow_reviewers` | companions | 0 | 528 | 2 | standard |
| `ddflow_companions_add` | `ddflow_reviewers` | companions_add | 0 | 699 | 4 | all |
| `ddflow_companions_verify` | `ddflow_reviewers` | companions_verify | 0 | 454 | 2 | all |
| `ddflow_complete` | `ddflow_complete` | (kept) | 19 | 1079 | 8 | core |
| `ddflow_configure` | `ddflow_admin` | configure | 0 | 1451 | 6 | standard |
| `ddflow_decision_add` | `ddflow_decision` | decision_add | 4 | 2029 | 16 | core |
| `ddflow_decision_applicable` | `ddflow_decision` | decision_applicable | 0 | 509 | 2 | standard |
| `ddflow_decision_list` | `ddflow_decision` | decision_list | 0 | 641 | 4 | standard |
| `ddflow_decision_show` | `ddflow_decision` | decision_show | 0 | 548 | 2 | standard |
| `ddflow_decision_supersede` | `ddflow_decision` | decision_supersede | 0 | 557 | 4 | standard |
| `ddflow_docs` | `ddflow_view` | docs | 0 | 647 | 3 | all |
| `ddflow_doctor` | `ddflow_doctor` | doctor | 2 | 310 | 1 | standard |
| `ddflow_dupes` | `ddflow_memory` | dupes | 0 | 973 | 5 | standard |
| `ddflow_export` | `ddflow_admin` | export | 0 | 1445 | 18 | all |
| `ddflow_external_sync` | `ddflow_admin` | external_sync | 0 | 567 | 1 | all |
| `ddflow_flow_choose` | `ddflow_workflow` | flow_choose | 0 | 802 | 4 | all |
| `ddflow_flow_show` | `ddflow_workflow` | flow_show | 0 | 575 | 1 | standard |
| `ddflow_gate_list` | `ddflow_gate` | gate_list | 0 | 461 | 3 | standard |
| `ddflow_gate_record` | `ddflow_gate` | gate_record | 104 | 1514 | 12 | core |
| `ddflow_gate_run` | `ddflow_gate` | gate_run | 12 | 494 | 3 | core |
| `ddflow_gate_skip` | `ddflow_gate` | gate_skip | 12 | 821 | 4 | core |
| `ddflow_gate_status` | `ddflow_gate` | gate_status | 28 | 438 | 2 | core |
| `ddflow_gate_verify` | `ddflow_gate` | gate_verify | 0 | 808 | 3 | core |
| `ddflow_health` | `ddflow_view` | health | 0 | 405 | 2 | all |
| `ddflow_heartbeat` | `ddflow_heartbeat` | (kept) | 12 | 365 | 2 | core |
| `ddflow_help` | `ddflow_help` | (kept) | 1 | 837 | 2 | core |
| `ddflow_history` | `ddflow_view` | history | 0 | 1050 | 7 | standard |
| `ddflow_hooks` | `ddflow_doctor` | hooks | 0 | 1082 | 4 | all |
| `ddflow_identify` | `ddflow_identify` | (kept) | 63 | 955 | 1 | core |
| `ddflow_import` | `ddflow_admin` | import | 0 | 1001 | 4 | all |
| `ddflow_import_verify` | `ddflow_admin` | import_verify | 0 | 698 | 1 | all |
| `ddflow_job_add` | `ddflow_job` | job_add | 0 | 673 | 5 | all |
| `ddflow_job_end` | `ddflow_job` | job_end | 0 | 735 | 5 | all |
| `ddflow_job_list` | `ddflow_job` | job_list | 0 | 639 | 3 | all |
| `ddflow_job_run` | `ddflow_job` | job_run | 0 | 1014 | 5 | all |
| `ddflow_lanes` | `ddflow_view` | lanes | 0 | 654 | 3 | all |
| `ddflow_lesson_add` | `ddflow_memory` | lesson_add | 5 | 2096 | 14 | core |
| `ddflow_lesson_search` | `ddflow_memory` | lesson_search | 0 | 471 | 3 | standard |
| `ddflow_lesson_verify` | `ddflow_memory` | lesson_verify | 0 | 509 | 1 | all |
| `ddflow_link` | `ddflow_memory` | link | 0 | 984 | 5 | standard |
| `ddflow_list` | `ddflow_view` | list | 0 | 1513 | 15 | standard |
| `ddflow_loops` | `ddflow_view` | loops | 0 | 656 | 1 | all |
| `ddflow_memory_add` | `ddflow_memory` | memory_add | 0 | 1179 | 6 | standard |
| `ddflow_memory_forget` | `ddflow_memory` | memory_forget | 0 | 547 | 3 | all |
| `ddflow_memory_list` | `ddflow_memory` | memory_list | 0 | 672 | 4 | standard |
| `ddflow_merge` | `ddflow_merge` | (kept) | 22 | 1479 | 9 | core |
| `ddflow_metrics` | `ddflow_view` | metrics | 0 | 1273 | 6 | all |
| `ddflow_next` | `ddflow_next` | (kept) | 5 | 654 | 3 | core |
| `ddflow_onboard` | `ddflow_admin` | onboard | 0 | 906 | 4 | standard |
| `ddflow_phase_add` | `ddflow_task` | phase_add | 1 | 1709 | 12 | standard |
| `ddflow_phase_close` | `ddflow_task` | phase_close | 0 | 731 | 3 | all |
| `ddflow_pins` | `ddflow_view` | pins | 0 | 1268 | 9 | all |
| `ddflow_pr_status` | `ddflow_reviewers` | pr_status | 0 | 372 | 1 | standard |
| `ddflow_pr_sync` | `ddflow_reviewers` | pr_sync | 0 | 725 | 2 | core |
| `ddflow_pr_threads` | `ddflow_reviewers` | pr_threads | 0 | 623 | 5 | standard |
| `ddflow_precommit` | `ddflow_doctor` | precommit | 0 | 852 | 3 | all |
| `ddflow_progress` | `ddflow_view` | progress | 0 | 657 | 3 | standard |
| `ddflow_promote_add` | `ddflow_ship` | promote_add | 0 | 675 | 3 | all |
| `ddflow_promote_deployed` | `ddflow_ship` | promote_deployed | 0 | 490 | 3 | all |
| `ddflow_promote_status` | `ddflow_ship` | promote_status | 0 | 324 | 1 | all |
| `ddflow_prompts` | `ddflow_admin` | prompts | 4 | 668 | 4 | all |
| `ddflow_queue` | `ddflow_view` | queue | 0 | 740 | 6 | standard |
| `ddflow_rebuild` | `ddflow_doctor` | rebuild | 0 | 310 | 1 | all |
| `ddflow_recall` | `ddflow_memory` | recall | 16 | 1187 | 5 | core |
| `ddflow_record` | `ddflow_doctor` | record | 0 | 468 | 2 | all |
| `ddflow_recover` | `ddflow_doctor` | recover | 4 | 733 | 3 | standard |
| `ddflow_release` | `ddflow_lease` | release | 5 | 579 | 3 | standard |
| `ddflow_remove` | `ddflow_task` | remove | 1 | 786 | 4 | standard |
| `ddflow_rename` | `ddflow_task` | rename | 0 | 935 | 6 | all |
| `ddflow_render` | `ddflow_view` | render | 1 | 599 | 3 | standard |
| `ddflow_replay` | `ddflow_doctor` | replay | 0 | 780 | 3 | all |
| `ddflow_research_add` | `ddflow_task` | research_add | 15 | 1791 | 14 | standard |
| `ddflow_resolve` | `ddflow_task` | resolve | 0 | 1072 | 4 | standard |
| `ddflow_review` | `ddflow_reviewers` | review | 20 | 2185 | 13 | core |
| `ddflow_review_triage` | `ddflow_reviewers` | review_triage | 3 | 1045 | 6 | standard |
| `ddflow_reviewers_detect` | `ddflow_reviewers` | reviewers_detect | 0 | 864 | 3 | all |
| `ddflow_reviewers_list` | `ddflow_reviewers` | reviewers_list | 4 | 274 | 1 | standard |
| `ddflow_rule_add` | `ddflow_rule` | rule_add | 0 | 1226 | 13 | standard |
| `ddflow_rule_edit` | `ddflow_rule` | rule_edit | 0 | 891 | 10 | standard |
| `ddflow_rule_list` | `ddflow_rule` | rule_list | 0 | 402 | 3 | standard |
| `ddflow_rule_remove` | `ddflow_rule` | rule_remove | 0 | 476 | 2 | standard |
| `ddflow_rule_search` | `ddflow_rule` | rule_search | 0 | 712 | 7 | standard |
| `ddflow_rule_show` | `ddflow_rule` | rule_show | 0 | 363 | 2 | standard |
| `ddflow_rule_sync` | `ddflow_rule` | rule_sync | 0 | 280 | 1 | standard |
| `ddflow_session_end` | `ddflow_session` | session_end | 4 | 537 | 3 | core |
| `ddflow_session_note` | `ddflow_session` | session_note | 5 | 684 | 4 | core |
| `ddflow_session_prompt` | `ddflow_session` | session_prompt | 23 | 640 | 4 | core |
| `ddflow_session_start` | `ddflow_session` | session_start | 5 | 394 | 3 | core |
| `ddflow_setup` | `ddflow_admin` | setup | 0 | 1019 | 3 | core |
| `ddflow_show` | `ddflow_show` | (kept) | 23 | 633 | 4 | core |
| `ddflow_similar` | `ddflow_memory` | similar | 0 | 905 | 3 | core |
| `ddflow_split` | `ddflow_task` | split | 0 | 1061 | 5 | standard |
| `ddflow_status` | `ddflow_view` | status | 2 | 543 | 1 | core |
| `ddflow_sync` | `ddflow_admin` | sync | 0 | 649 | 3 | standard |
| `ddflow_task_add` | `ddflow_task` | task_add | 25 | 2140 | 17 | core |
| `ddflow_task_fold` | `ddflow_task` | task_fold | 0 | 840 | 5 | all |
| `ddflow_tests` | `ddflow_gate` | tests | 0 | 778 | 3 | standard |
| `ddflow_unblock` | `ddflow_lease` | unblock | 0 | 761 | 3 | standard |
| `ddflow_update` | `ddflow_task` | update | 8 | 2118 | 13 | core |
| `ddflow_upgrade` | `ddflow_admin` | upgrade | 0 | 1552 | 14 | all |
| `ddflow_verify` | `ddflow_doctor` | verify | 0 | 917 | 10 | standard |
| `ddflow_version_cut` | `ddflow_ship` | version_cut | 0 | 949 | 8 | all |
| `ddflow_version_show` | `ddflow_ship` | version_show | 0 | 596 | 3 | standard |
| `ddflow_wait` | `ddflow_lease` | wait | 4 | 1354 | 7 | core |
| `ddflow_workflow` | `ddflow_workflow` | workflow | 0 | 591 | 1 | all |
| `ddflow_workflow_drop` | `ddflow_workflow` | workflow_drop | 0 | 718 | 3 | all |
| `ddflow_workflow_gate` | `ddflow_workflow` | workflow_gate | 0 | 1638 | 13 | all |
| `ddflow_workflow_pipeline` | `ddflow_workflow` | workflow_pipeline | 0 | 868 | 4 | all |
| `ddflow_workflow_state` | `ddflow_workflow` | workflow_state | 0 | 359 | 1 | standard |

## 6. Next-step hints in results

### Where ddflow already does it (CONFIRMED, read)

- `ddflow_next` returns the ready list; `ddflow brief` ends with "Suggested next" and the claim line.
- `gate status` prints `<item>: next = <current gate>` (ddflow/api/gates.py), and its remaining gates.
- Refusals name the fix ("You could take instead: ...", "Instead: ..." from the hook layer).
- Server notes: the first-call preamble and the once-per-session deprecation note ride on results
  (`ddflow/surfaces/mcp.py`), so a result-side channel already exists.

### Contract (proposal)

```
"next": [ {"tool": "ddflow_gate", "why": "rubber_duck is the first open gate",
           "args_hint": {"action": "run", "id": "<item>", "gate": "rubber_duck"}} ]
```

- At most 3 entries and 400 bytes per result (about 100 tokens); entries ordered by likelihood;
  `why` is one clause, 80 characters at most; `args_hint` carries only keys the caller cannot guess
  (ids, enum values), never free text from records (provenance fencing rules apply: a `why` built from
  a record title must be fenced or omitted).
- Emitted only by state-changing and queue-reading tools (claim, complete, merge, gate action=*, bug
  found/fixed, next, brief); absent on pure reads and on errors that already carry an instead-line.
- Derived from the same state the tool already computed (gate pipeline position, item state), never
  from a second query; `tool` must name an advertised tool in the current profile (a hint to a hidden
  tool is rewritten to its family action or dropped).
- Config knob `[mcp].next_hints = on|off|<max>`; CLI parity: `--json` carries the same field, text shows a
  one-line `next:`.

### Measured cost (CONFIRMED, bytes of the real MCP result against a compact hint)

| Result | Bytes today | + 1 hint (140 B) | + 2 hints (270 B) | + 3 hints (400 B) |
|---|---|---|---|---|
| brief | 4,417 | +3.2% | +6.1% | +9.1% |
| gate_status | 2,433 | +5.8% | +11.1% | +16.4% |
| show | 2,814 | +5.0% | +9.6% | +14.2% |
| recall | 5,163 | +2.7% | +5.2% | +7.7% |
| list (20 tasks) | 7,842 | +1.8% | +3.4% | +5.1% |
| next | 35,835 | +0.4% | +0.8% | +1.1% |
| status | 24,324 | +0.6% | +1.1% | +1.6% |

Savings are THEORETICAL until measured: the brief has no tool-choice prose to remove today, so the
saving is in avoided wrong calls and avoided `ddflow_help` round trips (each help costs 837 B plus a call),
not in a shorter brief. Measure (task 6) as wrong-call rate and calls-per-item with hints on versus off.
The tools/list saving (~51 KB) dwarfs any hint cost.

### Failure modes

- Stale hint: the world moved between result and next call (a parallel agent took the item); the hint must
  be advisory, and the target tool still re-checks state (it does: claim refuses).
- Over-following: an agent chains hints and skips judgment (merge before reviewers); hints never name a
  tool whose gate is unsatisfied, and `merge` is never hinted until `gate status` says complete.
- Prompt injection: a hint built from record text is an instruction channel; keep `why` to fixed templates.
- Hint to a hidden or aliased tool under a smaller profile; covered by the rewrite rule above.
- Token creep: a cap of 3 and 400 B, enforced by a test on every emitter.

## 7. Migration

- **Aliases.** `registry.Alias` records old name, new name, `since`, `removed_in`; `resolve_tool` maps a
  call, and `_notices` says once per session that the old name is deprecated (D-compat). Add one alias per
  old name carrying the implied `action`; aliased names are hidden from `tools/list` in every profile
  except the legacy flat ones. Window: two minor releases, then `removed_in` fails with the
  new-name hint. Today 114 aliases (A1).
- **Argument mapping.** Alias resolution must inject `action` and keep old argument names; the existing
  clash detection (argument given under both names) already handles collisions.
- **README tool table.** `tests/test_readme_tool_table.py` asserts one row per registry tool and the
  tier counts; it must render families (one row per family listing its actions) and the profile counts,
  from `ddflow.surfaces.tool_table`, regenerated with `uv run python -m ddflow.surfaces.tool_table README.md`.
  The test's "one row per `TOOLS` name" rule becomes "one row per advertised tool plus an alias list".
- **Tiers.** `CORE_TOOLS`, `STANDARD_EXTRA_TOOLS`, `FULL_ONLY_TOOLS` are keyed by tool name; make
  the family the unit (declared via `Command.tier`), and a profile is `{tool: actions}` data in the
  one table CP-mcp-profiles owns. `tests/test_mcp_tool_budget.py` keeps the CORE ratchet (40,000) and
  adds a per-profile report; the `lean` profile gets its own ratchet (30,000).
- **Upgrade manifest.** Ship the alias table and the new default profile as an upgrade entry so an
  existing project keeps flat names until it opts in or the window ends; docs and prompts that name
  tools (`docs/ddflow/drivers`, `CLAUDE.md` text, prompts) are rewritten by the generated-region mechanism.
- **Golden snapshots.** Snapshot `tools/list` per profile and the alias table; any family gain or loss
  of an action fails the test with the diff.
- **CLI/MCP parity.** The declared-command matrix (`ddflow/surfaces/declared/**`) stays the single source:
  a family is `group = noun`, an action is the verb, the parity ratchet that every CLI flag is reachable
  over MCP is checked per (family, action). A command with no family declares one (import fails otherwise,
  as `Command.tier` already does).
- **Hooks and agent text.** Pre-tool hooks and permission allowlists match tool names
  (`mcp__ddflow__ddflow_*`); a family call hides the verb inside arguments, so allow/deny rules that
  separate read from write (e.g. `ddflow_gate` list versus record) need an action-aware check. This is
  the largest hidden cost of families: record it in the follow-up for permissions.

## 8. Risks

1. Union params raise wrong-argument errors; mitigate with per-action required-field errors that name the
   missing fields (PM-search-call already specifies this) and per-action descriptions.
2. Host permission prompts and allowlists are per tool name: one `ddflow_gate` tool covers both read and
   destructive actions, so approving it approves all. Keep destructive actions (abandon, remove, rebuild,
   replay, upgrade restore) in families that read-only profiles omit.
3. Clients that cache tool lists across sessions will see names disappear; aliases cover calls, not lists.
4. Unmeasured model accuracy (section 3): make "wrong-call rate" part of the acceptance of task 6.
5. Descriptions get longer per tool; keep each family description to its action list plus one clause.
6. Cursor and Windsurf numbers are CLAIMED; read the vendor docs before hard-coding a cap into a descriptor
   (they are descriptor values the user can override anyway).

## 9. Ordered follow-up tasks

Waves are relative depth (GR-wave-derived makes the real value derived).

These tasks are filed as MTD-* (MTD-decision, MTD-family-spec, MTD-family-schema, MTD-aliases, MTD-profiles, MTD-accuracy-eval, MTD-next-hints, MTD-readme-table, MTD-budget-test, MTD-permissions, MTD-search-call, MTD-upgrade-and-docs, MTD-sunset) in phase P-tool-diet; the prefix MTD- avoids a clash with TD-, which belongs to P-test-domains. Research record: R4db11d8763 (CONFIRMED).

| # | Task | Globs | Needs | Wave |
|---|---|---|---|---|
| 1 | MTD-decision: operator decision superseding D-mcp-surface (1): adopt families (A1 provisional) and fix the deprecation window and default profile; the final A1 versus A2 choice is left to MTD-accuracy-eval | docs/ddflow/decisions (decision record only) | RM-tool-diet-research | 1 |
| 2 | MTD-family-spec: add `family` and `action` to `Command` in the declared matrix; import fails without one; generate the family/alias table from it (reuse `Command.tier`) | ddflow/surfaces/registry.py, ddflow/surfaces/declared/**, tests/test_declared_families.py | MTD-decision | 2 |
| 3 | MTD-family-schema: build family tool schemas (action enum, union params deduplicated, shared `as_agent` once, per-action required errors) in `tools/list` and `tools/call` | ddflow/surfaces/mcp.py, ddflow/surfaces/tools/families.py, tests/test_mcp_families.py | MTD-family-spec | 3 |
| 4 | MTD-aliases: one `Alias` per old name with implied `action`, hidden from lists, once-per-session note, `removed_in`; golden alias table | ddflow/surfaces/registry.py, ddflow/surfaces/tools/aliases.py, tests/test_mcp_aliases.py | MTD-family-schema | 4 |
| 5 | MTD-profiles: express tiers as profiles keyed by family/action and by descriptor cap (`lean`, `families`, `hot+families`, flat legacy); choose by `[caps]` and `clientInfo.name`; per-profile byte report. This IS P-agent-parity.tool-profile / CP-mcp-profiles: fold in, do not duplicate | ddflow/surfaces/tools/tiers.py, ddflow/surfaces/mcp.py, ddflow/config_sections/mcp.py, ddflow/harnesses/*.toml, tests/test_mcp_tool_profiles.py | MTD-family-schema, P-agent-parity.tool-profile | 4 |
| 6 | MTD-accuracy-eval: replay a fixed task script against flat 123, A1 and A2 with two model families; record wrong-call rate, calls per item and bytes; decides A1 versus A2 and the hint default | tests/eval/tool_diet/**, docs/ddflow/research/mcp-tool-diet.md | MTD-family-schema | 4 |
| 7 | MTD-next-hints: the `next` field contract, emitters for claim, complete, merge, gate, bug, next and brief, byte cap test, `[mcp].next_hints` knob, `--json` parity | ddflow/surfaces/mcp_next.py, ddflow/api/gates.py, ddflow/surfaces/mcp.py, tests/test_mcp_next_hints.py | MTD-family-schema | 4 |
| 8 | MTD-readme-table: README tool table and counts render families, profiles and the alias list; update tests/test_readme_tool_table.py | ddflow/surfaces/tool_table.py, ddflow/api/readme_tools.py, README.md, tests/test_readme_tool_table.py | MTD-profiles | 5 |
| 9 | MTD-budget-test: per-profile report, ratchets for `lean` (30,000) and core (40,000), warn-only for the rest (D-mcp-budget-report); goldens for `tools/list` per profile | tests/test_mcp_tool_budget.py, tests/golden/mcp/** | MTD-profiles, SE-mcp-budget | 5 |
| 10 | MTD-permissions: action-aware allow/deny for hooks and harness permission files; read-only profile omits destructive actions | ddflow/services/hooks*, ddflow/harnesses/*.toml, tests/test_mcp_action_permissions.py | MTD-profiles | 5 |
| 11 | MTD-search-call: reuse PM-search-call as the 2-tool escape hatch for caps below 24 (no second dispatcher); `ddflow_search` over family/action | ddflow/surfaces/mcp.py (facade only) | PM-search-call, MTD-family-schema | 5 |
| 12 | MTD-upgrade-and-docs: upgrade manifest entry (aliases, default profile, opt-in), rewrite tool names in drivers/prompts, deprecation notes | ddflow/upgrade/**, docs/ddflow/drivers/**, tests/test_upgrade_mcp_profile.py | MTD-aliases, MTD-profiles | 6 |
| 13 | MTD-sunset: after the window, remove aliases at `removed_in`, drop flat tiers from defaults | ddflow/surfaces/tools/aliases.py | MTD-upgrade-and-docs | 7 |

## 10. Reproducing the numbers

Inventory: build `Server(repo).handle({"method": "tools/list"})`, measure each tool's compact JSON,
count `inputSchema.properties`, place by `tiers.CORE_TOOLS` / `STANDARD_EXTRA_TOOLS`. Usage: regex
`mcp__ddflow__(ddflow_[a-z_0-9]+)` over `tool_use` lines of the transcripts (counts only). Result
sizes: call the tools through `Server.handle` on this repository. Family bytes: union of properties per
family plus an `action` enum. The scripts are throwaway (not committed); this note holds the results.

## 11. Accuracy eval result (MTD-accuracy-eval, 2026-10-23)

Harness: `tests/eval/tool_diet/` (`td_run.py REVIEWER [--retry] [--repeat N]`, hand-run; the offline
part is `test_tool_diet_eval.py`). 16 fixed plain-language requests (`td_scenarios.py`: claim, gate
record/skip/status, bug found/fixed, task add, lesson add/search, decision add, session prompt, release,
merge, complete, rule search, status), each asked twice per surface, one tool call each, graded on the member tool
reached and on the values carried (whole-token match); a family call is resolved by the server's own
`family_schema.route`, and a member name is not on the family surface. Models are two families through
their configured OpenAI-compatible endpoints: DeepSeek-V4.1-Flash and Kimi k3. 32 requests per cell, one
run per cell: a difference of two or three requests is noise (the same cell moved by up to 0.06 between
runs of the same harness; every number here is from the last run).

| Surface | Bytes shown | Prompt tokens (k3 / DeepSeek) | DeepSeek first call | Kimi k3 first call | DeepSeek +1 retry | Kimi k3 +1 retry |
|---|---|---|---|---|---|---|
| flat (123 tools) | 111,036 | 25,146 / 29,374 | 1.000 | 0.938 | 1.000 | 0.938 |
| families (24 tools) | 51,698 | 11,913 / 14,680 | 0.969 | 0.625 | 1.000 | 0.844 |
| families, arguments tagged with their actions | 56,740 | 13,651 / 16,147 | 0.969 | 0.656 | 0.969 | 0.875 |

Both surfaces are held to the same server-side argument check (required and unknown arguments), so a flat
call with an invented argument is as invalid as a family one.

(CONFIRMED, measured.) What it shows:

- Families halve the prompt, and DeepSeek reads them as well as the flat list (0.969 against 1.000 is one request).
- Kimi k3 loses about 31 points on the first call and about 10 after one retry. The loss is the union
  schema, not the choice of family: 8 of 32 first calls are refused by `route` (it fills arguments that belong to
  other actions: `verify_reason` on bug found, `worktree` on task add, `reviewer_model` and `timeout` on
  gate skip, or leaves out `gate`), each with a message that names the action.
- Tagging each argument with its actions barely helped Kimi (0.656 against 0.625 on the first call; the
  retry moved by one request): the noise is the 18 to 43 union arguments.
- `ddflow_status` was mistaken for `ddflow_board` by DeepSeek on the flat list only.
- The `next` hint default (MTD-next-hints) was NOT evaluated, so by D-mcp-tool-families it stays off.

Consequence under D-mcp-tool-families ("if families lose measurable call accuracy against flat tools the
default reverts to flat plus profiles"): they lose on one of the two families, so `families` MUST NOT
become the default profile until the operator accepts that (MTD-profiles keeps the flat default and
ships `families` as an opt-in profile). Levers to try that keep the byte saving: fewer union arguments per
family (move the seldom-used ones into one `options` object, as section 4 suggested), splitting the
widest families (`task` 43, `admin` 43, `view` 40 arguments) so no tool carries more than about 20, and
the `hot+families` profile (36 tools) for clients that like direct names for the most-called verbs.
