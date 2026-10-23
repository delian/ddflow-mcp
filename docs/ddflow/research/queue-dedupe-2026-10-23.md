# Queue dedupe review, 2026-10-23

Operator request (verbatim, session prompt): "Make a review again on bugs, phases, tasks, make sure dependencies are set to prevent locking and cycling dependency but also to allow parallel development as much as possible, check for duplication, extensions, merge, contradictions, and specify properly the wave tags". This note is the duplicates / extensions / merges / contradictions half (agent qa-dupes); dependency edges, cycles, globs and wave tags are qa-graph's.

Method: ddflow dupes / similar / search over bugs, 3,000 tasks (1,563 open), 151 phases, 319 decisions, rules, lessons, memories, research; own scripts over the event log (title/body similarity, shared globs, id cross-references, existing links); every close was made with `bug fixed --landed` (test fails on the commit's first parent, passes on it) or `abandon` with commit and test named. Nothing was deleted; nothing claimed or in flight was touched.

## Counts
- Bugs closed with proof: 4 (3 stale-on-main or duplicate of fixed: B2ee8585e2d, Ba502f5434a, B630ffef2d8; 1 duplicate of fixed B587e607ad9: Bd805c2f648). Bug duplicates linked: 3.
- Tasks abandoned as done in effect: 4. Throwaway tasks abandoned: 2 (T1, T2).
- Tasks folded: 8 (CP-mcp-profiles, P-agent-parity.tool-profile into MTD-profiles; DX-split-leases-module; DX-refusal-names-approval-route; DX-brief-pending-for-operator-in-cloud; ID-vocab-aliases; fix-B0689d19f95; DX-wave-phase-prereq).
- Links recorded: about 60 (related/extends/duplicate_of). Decisions superseded: 3. Memory forgotten: 1.
- Filed: 6 DECIDE tasks (QD-decide-*), 9 DX gap tasks.

## Table
| id | verdict | evidence | action taken | action deferred |
|---|---|---|---|---|
| B2ee8585e2d | stale | pin test_the_setup_and_operations_cli_transcript_is_unchanged passes on main; pins regenerate --check all current; fix landed 38787ee06 | bug fixed --landed 38787ee06 (verified); fix task removed | - |
| Ba502f5434a | stale | test_every_command_printed_advice_names_exists passes on main 8ee27efe9; `approvals approve/request` CLI added 0378647ce | bug fixed --landed 0378647ce (verified fails on parent, passes on it); fix task removed | - |
| B630ffef2d8 | duplicate (of Ba502f5434a) | same failing test, same advice text | link --duplicate-of; bug fixed --landed 0378647ce (verified) | - |
| B0689d19f95 / B53b3b594b3 | duplicate | pins regenerate rewrites .gitattributes | link --duplicate-of; fold fix-B0689d19f95 into fix-B53b3b594b3 | bug closes with regression test of fix-B53b |
| Bff1731a132 | stale | already fixed by its in-flight task during this run | none | - |
| B93f74bc125 | keep (still red) | 4 tests fail at 8ee27efe9: test_import_verify handshake, test_registry, test_no_agent_literals, test_handshake_recoverable | none | fix-B93f74bc125 in queue, high severity |
| MTD-profiles / P-agent-parity.tool-profile / CP-mcp-profiles | duplicate (subsumed) | MTD-profiles body says "This IS ... fold them in" yet needed them (unrealisable) | task fold CP-mcp-profiles, then P-agent-parity.tool-profile into MTD-profiles; restored need on P-agent-parity.descriptor-v2 that tool-profile carried | - |
| DX-split-leases-module / -package | duplicate | same file, same split | task fold | - |
| DX-refusal-names-approval-route -> RA-prompt; DX-brief-pending-for-operator-in-cloud -> RA-brief | duplicate (self-declared folded) | bodies say "Folded into RA-prompt/RA-brief; filed so it is not forgotten" | task fold | - |
| DX-suite-host-lock | stale (done in effect) | 9ac6b9375: suitelock.py, [ci].max_concurrent_suites, tests/test_ci_parity_workers.py, test_suitelock_queue.py | abandon with reason; dropped 3 dead needs edges (B-af-doctor, B-af-acceptance, B-par-process-isolation) | residual gaps are bugs Bf37f016693/B7ad0d907d4/B401874645d |
| DX-verify-fix-already-landed | stale (done in effect) | bug fixed --landed (6a86d15ae), tests/test_bug_fixed_landed.py::test_a_fix_landed_elsewhere_is_verified_against_its_landing_commit | abandon with reason | - |
| DX-list-paging | stale (done in effect) | b05ce7372: tests test_limit_above_the_cap_says_it_was_clamped, test_offset_walks_the_rest_and_next_ends; `list --offset` live | abandon; dropped dead edge on DX-record-verb-table; B-list-cap-hardcoded-and-search-note linked related | `--all` help wording residual |
| ID-vocab-aliases / B-id-terms | duplicate | both build ddflow/services/vocabulary.py + [vocabulary] kind->term aliases | task fold ID-vocab-aliases into B-id-terms; added needs B-org-layers, ID-decide | - |
| Bd805c2f648 | duplicate (of fixed B587e607ad9) | note-bug about the DeepSeek chunk non-convergence; fix 0d99adf99 | bug fixed --landed 0d99adf99 with B587's test (verified) | - |
| RV-split-cap | stale (done in effect) | 0d99adf99 splits a truncated chunk and caps tokens; tests/test_review_split_nonconverging.py::test_a_chunk_that_never_converges_is_split_in_halves_and_reviewed | abandon with proof | - |
| T1, T2 | stale (throwaway) | empty title/body, glob a.py | abandon | phase P1 ('P') cannot be retired: no phase abandon (DX-phase-abandon) |
| D1, D-usage-pacing-2026-10, D-unify-first | stale decisions | test artefact; window ended 2026-10-15; P-unify done 217/217 | new D-spent-decisions-2026-10-23, supersede x3 | 6 more spent decisions: QD-decide-stale-decisions-memories |
| M801fd9dc2b | stale memory | local config schedule.parallel = auto | memory forget | Me5db019bea, M1da6c78dd8: same DECIDE task |
| Bfc43dae5b9 | extends | RM-progress-everywhere emits progress from ddflow_wait too | link related | closes with RM-progress-everywhere's test |
| B74d5c39e6e | extends | RQ-note-deliver pre-refutes identical digests | link related | - |
| B10ad449deb, Bfdcb5c177c | related | review rounds charged without verdict/change | linked | DX-reviewer-chunk-truncation extends fixed B587 and relates Bfdcb5c177c |
| Bee5f08be7b, B8fb4a2ec4a, Bb49d834e90, Bd10386f03b | related (one design) | bug without fix task / regression verify on a test-only fix | linked | fix once, close the four with one regression |
| Bmcp-task-add-no-kind | duplicate of B-doc-task-kind-not-settable-after-add | both: no kind on MCP task_add; fix-B5df7832d0a is the shared update fix | link duplicate_of | fix-Bmcp-task-add-no-kind removed after fix-B5df7832d0a lands (in flight) |
| B-update-cannot-reparent-task, DX-update-set-phase, P-reshape.move | extends | same missing update --phase | linked | - |
| B-mcp-required-falsy, B-mcp-no-type-enum-check, MCPP-input-schema | related | one MCP boundary check | linked | - |
| B7ad0d907d4, B401874645d, Bf37f016693, Bf7f6540615 | related | host suite-lock / atomicity cluster | linked | Bf37f016693 in flight |
| B9834dabc40 | keep | still reproduces: `ddflow list task --limit 1000 \| head -1` prints a BrokenPipe traceback | - | - |
| B-batch-integration | extends/subsumed by BG-ci | D-BG-Q6 (operator): fold when BG-ci starts; fold would re-point 4 B-par-* needs | none | fold at BG-ci start |
| B-id-terms / ID-vocab-aliases | duplicate | see above | fold | - |
| PD-req-kind | extends RQM-model | D-requirements-model: ONE requirement kind, same requirement.toml | note appended, link extends | edge PD-req-kind -> RQM-model (qa-graph) |
| B-res-* (P-resume) vs DX-recovery-*/DX-resume-command (P-orphan-recovery) | duplicate phases | two `ddflow resume` tasks, [recover].auto vs [recover].enabled, two brief blocks; research note never mentions P-resume | 5 pairs linked related | QD-decide-resume-vs-orphan |
| LB-waves, GR-wave-derived, DX-waves-status | extends | three wave computations; D-wave-semantics | LB-waves extends GR-wave-derived | - |
| SPEC/US/RQM/PD/CN/SDD/TF-trace, CN/PD/SPEC-baseline | extends | one suspect-link mechanism (SPEC-reconcile plans it) | linked to SPEC-trace/SPEC-baseline | SPEC-reconcile executes |
| RQM-/SPEC-/US-dupes | extends | already coordinated | none | - |
| AP-budget, SM-budget, FL-review-cap | extends B-sched-backpressure | one backpressure engine | linked | - |
| B-sub-mcp-guard, MTD-permissions, MCPP-effect-class | extends | one filter engine, one effect table | linked | - |
| DX-tools-list-budget-doctor-brief | contradiction (core/standard/all tiers vs profiles) | MTD profiles; doctor line exists (LS-measure) | linked to MTD-budget-test | reword at MTD-budget-test |
| B-perf-mcp-start | extends (mostly subsumed) | tool-list shrink = P-tool-diet, initialize fold = SU-init-fast/test_mcp_initialize_latency | linked | narrow to the resident-State cache |
| DX-gate-not-applicable | extends D-docs-gates-not-applicable | gate record has no not-applicable outcome | linked | implement |
| DX-author-family / B-complete-requires-model-even-when-recorded | duplicate | same --model refusal | linked both ways | - |
| B111, B112 | keep | docs-only research notes sharing docs/RESEARCH.md; each behind ST-merge-strategy / ST-locality | none | graph question for qa-graph |
| B197, B199, B202 | keep | chained knowledge features (recurrence, edit/retire, priority); distinct deliverables, shared model.py | none | order only |
| OP-chore-approve | keep | operator step waiting since 2026-10-17 | none | operator |
| D-compat-r2 vs D-mcp-tool-families vs D-DEP-Q1 | contradiction, resolved | D-DEP-Q1: stable until 1.0, beta (MCP names) two releases + 30 days | links D-DEP-Q1, D-mcp-tool-families extends | DX-decision-amend-relation |
| D-mcp-surface-r2 'no tool renamed or merged' | contradiction, amended by D-mcp-tool-families | its own text says it amends | link extends | same DX |

## Contradictions needing the operator (DECIDE tasks, none decided by me)
1. QD-decide-review-rounds: cap 4 (D-review-rounds-4, 5 rejected) vs "4 or 5", shipped default 2, RV-round-cap default 2, leads authorising a 5th. Default: shipped 2, project 4, 5th only by --force --reason; drop RV-round-cap.
2. QD-decide-always-on-budgets: LS-charter 1.5K/1K chars vs requirement charter 500 tokens, GP-brief, RESUME block, recovery slice, tool-list bytes 95-100K vs 30/40K. Default: one total, SessionStart one dynamic line.
3. QD-decide-resume-vs-orphan: default D-orphan-recovery governs; fold four pairs.
4. QD-decide-new-nouns-vs-tool-diet: about 17 tasks add their own MCP tool vs 24-tool families; about 80 carry the voided "45 bytes of headroom" line. Default: actions of one record family.
5. QD-decide-layer-orders: requirement layers org>team>project>local>task vs D-config-layer-order. Default: a view of it.
6. QD-decide-stale-decisions-memories: six spent decisions, reviewer-holder (three accepted records vs kimi-k3 in memory), stale memories, OP-chore-approve.
Also known and already queued: strength vocabularies (MUST/SHOULD/MAY vs recommended/best-practice/required/forbidden) in GP-inventory/GP-decide.

## For qa-graph
- Folding P-agent-parity.tool-profile into MTD-profiles dropped its need on P-agent-parity.descriptor-v2; restored by hand. MTD-profiles needed the tasks it declared itself to absorb (unrealisable) - fixed by the folds.
- Dead edges removed after abandoning DX-suite-host-lock (B-af-doctor, B-af-acceptance, B-par-process-isolation) and DX-list-paging (DX-record-verb-table).
- B-id-terms gained needs B-org-layers, ID-decide (carried from ID-vocab-aliases); ID-vocab-aliases needed ID-phase-prefix without cause.
- PD-req-kind has no edge to RQM-model although both edit requirement.toml.
- Prefix ID- is used by two phases (P-id-consistency and P-continuous-improvement); note appended to ID-phase-prefix.

## ddflow gaps filed
DX-dupes-sweep-noise, DX-dupes-structural-signals, DX-decision-amend-relation, DX-fold-carries-needs, DX-abandon-dead-needs, DX-phase-abandon, DX-stale-bug-sweep, DX-done-in-effect-state, DX-memory-verify-expiry. Existing and relevant: B-coh-contradiction/sweep/resolve (decision contradiction detection), B-dup-closure (no `bug duplicate` verb), feat-batch-link, DX-decision-retire.
