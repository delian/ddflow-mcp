# Queue review 2026-10-23: dependency graph and waves (qa-graph)

Operator 2026-10-23, verbatim: "Make a review again on bugs, phases, tasks, make sure dependencies are set to prevent locking and cycling dependency but also to allow parallel development as much as possible, check for duplication, extensions, merge, contradictions, and specify properly the wave tags".

This is the graph half (dependencies, locking, parallelism, wave tags). Duplicates, contradictions and merges across bugs, tasks and decisions are the other agent's (qa-dupes); suspects met on the way are listed at the end. Every change below is a logged `ddflow batch` (atomic, cycle-checked by ddflow) with a session note giving the reason. Tooling: `scripts/wave_stamp_accurate.py` (stamps waves) and `scripts/queue_width.py` (width, critical path, lanes, `--what-if`).

## Result

| | before | after |
|---|---|---|
| open phases / open tasks (task, doc, research) | 116 / 1581 | 116 / 1586 (+6 follow-ups, +1 report, -2 completed meanwhile) |
| needs cycles (own, inherited phase needs, phase-contains-task) | 0 | 0 |
| live needs edges (to open items) | 3169 | 3212 |
| satisfied needs edges (to done items) | 1172 | 1047 (116 removed from 42 phases) |
| needs to unknown or abandoned ids | 0 | 0 |
| tasks with a correct `wave:N` tag | 366 | all but 14 (in flight, skipped) |
| tasks with a wrong tag / none | 890 / 325 | 0 / 0 outside the in-flight 14 |
| tasks at wave 0 (ready, ignoring the parallelism cap) | 298 | 300 |
| critical path (waves) | 38 | 37 |
| decide tasks starved by a phase hold | 3 (ACF-, PI-, SPEC-decide) | 0 |

Edges: 63 added, 144 removed (116 of those satisfied, 28 live). Tags: 1201 restamped in the main pass, 28 in a second pass for new and drifted items.

## Convention followed, and the one precision

D-wave-semantics (accepted) and D-labels: `wave:N` is derived and reserved; wave 0 is ready (no open prerequisite, task-level or inherited from the phase); wave N is 1 + the highest wave among open prerequisites; priority orders items inside a wave and never moves one across; the stamp ignores glob overlap. Until `ddflow waves --stamp` exists a script restamps with `ddflow update --add-tags/--remove-tags` (restamp_api.py, now retired by wave_stamp_accurate.py).

One precision, stated rather than hidden: restamp_api.py computed the depth of a phase used as a prerequisite from the phase's own needs only. A task behind a whole-phase hold such as P-docs-generic was therefore stamped wave 1 although it cannot start before that phase's 9-deep chain finishes (wave 10). The decision text says "open prerequisites", and a phase is open until its last task is done, so the stamp now uses wave(phase) = max over its open tasks. Doc and research kinds are stamped too (restamp_api.py stamped `kind == "task"` only). Effect on the old tags: of 1581, 366 were right, 792 were too low (by 2 to 28, mostly 6 to 20), 98 too high, 325 absent. This is filed as DX-wave-phase-prereq so GR-wave-derived implements the same rule. In-flight items (13) were not touched.

## Findings

(a) Cycles. None, on the graph that includes inherited phase needs and "a phase waits for its tasks" (Tarjan plus a depth pass, before and after every batch). No cycle through lease conflicts is statically possible: a lease conflict orders two tasks at run time and never adds a wait the needs graph lacks; the live waits are `Waiting for files` in `ddflow status` (fx-cilock on LS-worker-mcp-config, 1285 s) and the self-heal rule r-lock-cycle-selfheal covers them. `ddflow loops` went from 11 to 9 findings (the four `duplicate_work` groups remain: they share files, and two are real duplicates).

(b) Dead edges. 0 needs point at unknown ids; 0 at abandoned items. 1172 pointed at done items (116 on phases, 1047 on 596 tasks). The phase ones were removed (B-gap-log-scale, B-gap-lanes, B-gap-plan-lint on 31 phases, P-unify on 11, and four others); they made "waits for six ids" read as a live hold. The task ones were left: inert, and removing them is about 600 events (DX-prune-satisfied-needs asks the operator's choice).

(c) Over-serialisation and starvation.
- The docs-wave hold (D-old-work-behind-core-docs-accepted, with the -r2 exceptions) is the largest single shape in the graph: 29 phases and 44 tasks need P-doc-templates, P-docs-generic, P-roadmap and P-docs-search, whose longest chain is 9 deep, so 896 of 1581 open tasks (57%) have it among their prerequisites. It is an operator decision and was NOT changed (see below). Measured effect of lifting the phase-level hold: critical path 37 to 30, total depth 18148 to 12247 (-33%).
- Decide tasks behind that hold: ACF-decide (inherited from P-accrete-fit), PI-decide and SPEC-decide (their own needs on the four docs phases, copied from the implementers) could not start; lesson L-needs-on-decide-tasks and DX-decide-tasks-blocked-by-phase-hold are this class. Fixed by moving the hold from the decide task to its direct dependents (PI-facts, PI-session-ingest, SPEC-model, five ACF-* implementers), so the implementation still waits for the docs wave and the operator can be asked now. No other decide, research or probe task is held only by the phase hold (B-rules-inventory, CM-inventory and DEP-inventory are implementation).
- Bugs and DX fixes: no `fix-` or `DX-` task is held by a phase hold (they have no phase). Six DX/fix to feature edges exist; five are real data dependencies (DX-gate-run-detach and DX-review-detach need RM-runs-core; DX-recovery-launch-lane needs P-agent-parity.launcher; fix-B-mcp-no-type-enum-check needs MCPP-input-schema, whose enum/min/max it enforces; DX-brief-pending-for-operator-in-cloud needs RA-brief). One was a file-sharing edge only: fix-B-mcp-required-falsy needed the feature MTD-next-hints; removed (RH rule: never a needs edge from a fix/DX task to a feature task; the lease already serialises mcp.py).
- Whole-phase needs: 146 task edges point at a phase (38 each at the three docs phases, 11 each at P-decisions and P-org-config from P-reasoning and three P-gaps tasks, five EXT-* tasks, SDD-export-tasks to a one-task phase). The five EXT-* tasks each named one building block in their text and waited for 7 to 31 tasks: EXT-task-budget needed all of P-governor (wave 38) and now needs P-governor.sampling (24); EXT-oh-lane P-acp to CP-backend-iface; EXT-procedure-export P-platform-mcp to PM-descriptor; EXT-ticket-to-pr P-autopilot to AP-classes; EXT-sprint-trains P-labels to LB-engine. The block-picks are judgements from the task text and the phase's task list; they are easy to widen.
- SE-globs-agent-phases needed SE-globs (depth 4: SE-event-registry, SE-mcp-budget, the README split decision) although it needs only the plan checker (done). Its own text says eight entry tasks must wait for it, so keeping that need would have delayed the 67 tasks of P-ai-import, P-acp and P-platform-mcp by four waves. The SE-globs need was dropped and the entry edges added.
- Transitively redundant own edges: 602 (590 task, 11 phase, 1 doc), for example P-test-robustness listing the docs phases and P-decisions. They do not change any wave. Left: removing an edge whose intermediate edge is later dropped silently loses the dependency; RH-prune-needs is the owner and should report, not auto-apply.

(d) Missing edges.
- Producer text that says "add needs": HX-facts-core (CONSUMERS list), SE-globs-agent-phases (entry tasks), B-tpl-readme-regions (consumers to add), B-sched-backpressure (consumers add needs). 21 edges were missing and were added: HX-facts-core to OB-verify-facts, AC-spike, PL-probe, P-spawn.facts, PM-verify, P-governor.facts, RF-native-descriptor, P-context-sync.doors; SE-globs-agent-phases to eight entry tasks; B-tpl-readme-regions to P-change-surface.docs-fragments, RH-docs-engine, B-dt-unify; B-sched-backpressure to AP-kill and FL-review-cap. The other consumers already had the edge.
- Shared files: 9185 pairs of open tasks share a literal file with no order between them (README.md 2131 pairs over 71 tasks, ddflow/surfaces/mcp.py 1683 over 63, ddflow/config.py 1137 over 54, core/model.py 392, services/review.py 374, implement-phase.md 348, core/events.py 309). These are not made safe by edges: a 71-task README chain would serialise every docs section. 97 pairs share three or more files; ordering was added only for the three same-phase pairs sharing five files (B-roborev-poll-cap before B-reviewer-pool, B199 before B197 before B202), plus two from the loop findings (DX-gate-record-withdraw after the bug fix fix-B2e324cdb87, DX-descriptor-family-doc after DX-descriptor-vocab-dsh). The rest is left to `schedule.auto_order`. DX-readme-glob-diet addresses the cause.

(e) Broad globs. No task lists `ddflow/**`, `**`, `ddflow/api/**`, `ddflow/services/**` or `tests/**`. The widest are directory globs of one package (`ddflow/api/lifecycle/` 28 tasks, `ddflow/services/gates/` 23, `ddflow/api/reporting/` 21, `ddflow/api/knowledge/` 19) and B-quota-docs (`docs/**`, left: its docs files are unknown from the text). 448 open tasks declare no globs and so have no conflict protection; 67 of those are the P-ai-import, P-acp and P-platform-mcp tasks that SE-globs-agent-phases covers.

(f) Critical path (37 waves, set by order decisions, not by technical need): B-rc-lifecycle-guard, then the document engine B-dt-events, -ownership, -interview, -surfaces, -library, -onboard, -unify, -acceptance and the phase P-docs-generic (wave 9); P-decisions chain (B-org-bundle, SC-catalogue, B-dependency-upgrades, B-dec-deps, -presets, -infer, -acceptance; phase at 17); P-agent-memory chain (B-am-harness-probe, -ingest, -capture, -handoff, -search, -session-cli, -injection-tests, -acceptance, -docs; phase at 27); B-sc-tree, B-sc-transfer, P-governor.actions, GV-preset, -guardrails, -roles, -workflows; RF-effort, RF-docs, RF-acceptance. Three consecutive phase steps (the docs wave, then P-decisions, then P-agent-memory, each needing the whole of the one before) are 28 of the 38 nodes. What-if (script, nothing applied): drop the two whole-phase needs from P-agent-memory and P-agent-compat: 37 to 29; lift the docs hold from every phase: 37 to 30.

Width per phase (tasks / ready now / longest chain inside the phase / widest level), selected: P-tool-diet 11/3/4/4; P-lean-start 10/4/5/5; P-soft-upgrade 14/3/7/4; P-remote-approvals 15/7/4/8; P-batch-gates 12/1/8/3; P-graded-policies 14/1/9/4; P-dsh-fit 15/4/5/7; P-id-consistency 9/3/5/3; P-doc-templates 8/7/2/7; P-docs-generic 14/2/8/3; P-docs-search 8/2/4/3; P-roadmap 5/0/4/2 (waits for P-doc-templates); P-requirements 25/2/7/8; P-user-stories 25/1/10/5; P-specs 20/1/8/5; P-accrete-fit 8/1/4/5; P-pidev-fit 12/1/6/4; P-commit-messages 21/0/8/5 and P-deprecation 12/0/8/3 (held behind the docs wave, wave 10). The full table is `python3 docs/ddflow/research/scripts/queue_width.py`.

(g) Phases and orphans. No phase is empty or can close. 182 open items have no phase: the B-* backlog, 20 `fix-` tasks and the DX tasks (a convention here; the DX tasks are 107 of the 286 ready now). Phase `P1` with the empty task `T1` looks like a leftover test fixture (qa-dupes). Phases of one task: P-readme-structure, B-export, BL-b87b95..., BL-coverage-gaps, P1.

(h) Wave tags: see "Convention". After the pass every open task outside the 14 in flight carries exactly one `wave:N` equal to the computed depth. Before: 792 tags too low (behind whole-phase holds), 65 one too high, 33 two to four too high, 325 untagged.

## What was changed

1. Batch 1a: PI-decide, SPEC-decide, P-accrete-fit (phase): removed the four docs-wave needs and the done B-gap trio; added the four docs needs to PI-facts, PI-session-ingest, SPEC-model, ACF-why-export, ACF-verify, ACF-memory-consolidate, ACF-recall-probe, ACF-consult-out. +32 -21.
2. Batch 1b: the 21 producer-listed edges and the SE-globs drop above. +21 -1.
3. Batch 2: fix-B-mcp-required-falsy no longer needs MTD-next-hints. -1.
4. Batch 3: five EXT-* tasks narrowed from a phase to a task. +5 -5.
5. Batch 4: 116 satisfied needs removed from 42 open phases. -116.
6. Batches 5 and 6: five ordering edges for shared-file pairs. +5.
7. Wave pass: 1229 `wave:N` tags (1201 + 28).
8. Follow-ups filed (all `dx`): DX-wave-phase-prereq, DX-queue-lint-edge-classes, DX-prune-satisfied-needs, DX-parallel-width-report, DX-readme-glob-diet, DX-queue-holds-decide (an operator question with the three measured holds).

## What was not changed, and why

- The docs-wave hold on the 29 phases and 44 tasks that keep it: D-old-work-behind-core-docs-accepted is an explicit operator decision whose rejected alternative is the one this review would otherwise pick. DX-queue-holds-decide asks, with the numbers above.
- P-agent-memory and P-agent-compat needing the whole of P-decisions and P-org-config (operator order in D-phase-order-gaps-first). Same task.
- B-gap-spend (wave 20), B-gap-command-trust and B-gap-checks-publish (wave 18) wait for P-decisions and P-org-config although P-gaps is "core first"; B-gap-command-trust is a research-first security question that needs neither and is `blocked` on purpose. Same task.
- The 602 transitively redundant edges, the 1047 task-level satisfied edges, and all other 94 shared-file pairs (above).
- Anything in flight (13 items: MTD-aliases, MTD-family-route, SU-client-table, LS-worker-mcp-config, RA-probe-harness, RA-channel-chat, RA-consume-sites-2 and six fix- tasks): tags only, no edges. fx-cilock waiting for LS-worker-mcp-config's files is the one live wait; it resolves when that merges (no cycle).
- The B/BL backlog items that are `blocked` on purpose (B13, B14, B95, B109-B112, B175).
- B-quota-docs' `docs/**`.

## The next three waves, with lanes

Wave 0 is 286 ready tasks in 182 file-independent lanes (one lane of 88 joined through mcp.py, config.py, setup.py, events.py; everything else is lanes of one to four), so the cap (`parallel` 13 to 20), not the graph, is the limit. Order within the wave by this rule: bugs first, then the spine, then the rest by priority.

- Lane 1, bugs: the 38 `fix-` tasks and the open bug fixes of `bugs-2` (22 ready), by priority. Take red-main fixes first.
- Lane 2, the spine, never idle: B-rc-lifecycle-guard (wave 0), B-dt-events (1), then B-dt-ownership and B-dt-obligations (2), with the seven independent P-doc-templates tasks (wave 0, B-tpl-*) beside it. This is the 9-deep chain that gates 896 tasks.
- Lane 3, locking and ordering DX (r-lock-cycle-selfheal): the original priority-1 items are done; what remains at wave 0 is DX-lease-yield-doctor-status, DX-lease-hunk-optimistic, DX-lease-lazy-acquire, DX-lease-real-overlap, DX-lease-release-early, DX-merge-base-one-path, DX-merge-force-failed-ci, DX-pins-driver-conflict-msg, by priority.
- Lane 4, open phases already in flight: P-tool-diet (3 ready; wave 2 adds 3), P-lean-start (4 ready), P-soft-upgrade (3), P-remote-approvals (7 ready, the widest), P-dsh-fit (4), P-id-consistency (3).
- Lane 5, shared engines that unblock many: P-gaps (7), P-shared-engines (5), P-extension-seams (4), P-core-records, P-schedule (5), P-agent-hygiene (4), P-roadmap-hygiene (6). Their shared files make this lane two or three agents, not seven.
- Lane 6, the decisions: ACF-decide, PI-decide, SPEC-decide (now wave 0), US-decide, ID-decide, RF-native-decide (all wave 0): batch the operator's answers in one sitting; they free P-user-stories, P-id-consistency, P-pidev-fit, P-specs, P-accrete-fit.

Wave 1 (136 tasks, 97 lanes) is dominated by P-graph (8), P-change-surface (7), P-requirements (6), P-agent-parity (5), P-schedule (4), P-core-records (4), P-orphan-recovery (4) and 24 loose DX/backlog items; its largest file-joined lane is 25. Wave 2 (89 tasks, 81 lanes) is almost fully parallel: P-continuous-improvement (5), P-remote (4), P-schedule, P-config-engine, P-history-collapse, P-tool-diet, P-dsh-fit (3 each), and the second link of the docs spine. From wave 3 on, the docs spine and the 87 tasks at wave 10 (everything released when P-docs-generic closes) are the structure to watch.

## Remaining risks

- The restamp is a snapshot: closing any item restamps its dependents, so tags drift by the hour until GR-wave-derived makes the value derived. `python3 docs/ddflow/research/scripts/wave_stamp_accurate.py --apply` is idempotent.
- The block-picks for the five EXT-* tasks and the SE-globs drop are judgement from text: a wrong pick starts a task before a real prerequisite. Both are one `ddflow update --add-needs` to widen.
- README.md, mcp.py and config.py are the real parallelism ceiling. More edges will not help.
- The ddflow MCP server may be stale for some agents; the shell CLI was used throughout.

## Suspects for qa-dupes (not acted on)

DX-split-leases-module and DX-split-leases-package (same goal, same globs); B111 and B112 (same two docs files, both blocked); DX-gate-record-withdraw, DX-gates-api-dedupe and fix-B2e324cdb87 (same file; ordered now, but check the overlap); DX-descriptor-family-doc and DX-descriptor-vocab-dsh (same files; ordered); B197, B199 and B202 (same five files, a knowledge-lifecycle split worth checking for merge); GR-wave-derived, DX-waves-status, LB-waves and DX-wave-phase-prereq (four tasks around wave derivation, one engine); RH-prune-needs and DX-prune-satisfied-needs (different, but check); DX-queue-lint and DX-queue-lint-edge-classes; phase P1 / task T1.
