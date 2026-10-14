# Roadmap review

Decision document for the task `RH-decide`. It restates the records below; the records in the event log are the source of truth (`ddflow decision show D-roadmap-review`). Evidence: research note R-roadmap-review-2026-10.

## D-roadmap-review

```
D-roadmap-review — Roadmap review: reviewer rule, upgrade reading, managed daemons explored, one order decision, README front door, engines first, merges allowed, three new areas built
  status accepted · decided by operator

Context:
  Operator 2026-10-14 asked for a review of every open phase and task as one roadmap and of the decisions; note R-roadmap-review-2026-10 lists the evidence.

Decision:
  Operator 2026-10-14 (AskUserQuestion, RH-decide). (1) REVIEWER RULE: operator-authored subagent definitions with an explicit other-family model id count as an independent reviewer with no approval; agent-authored or tool-widening definitions need operator approval by digest (B-sub-trust); D-reviewer-trust is amended to point here. (2) UPGRADE: session start only prints the one-line notice ([upgrade].auto=check); on MCP connect the agent proposes the upgrade and the operator consents; container start applies safe categories only, and only when [upgrade].auto is safe. (3) DAEMONS, verbatim: 'I want to explore the option of ddflow to be able to start daemon if it decides, in addition to the operator, with notification to the operator, unless this is forbidden by config, or policy, and ddflow to be able manage the daemon life stages, start, stop, monitor, attach, disconnect, etc - whatever is expected'. Followed by the answer to the policy question (Allowed by default for a declared list; notify, the recommended option): config lists the daemon kinds ddflow may start itself; each start is logged and shown in brief and notification sinks; config, policy or [daemon].auto=off forbids it; a kind not on the list needs the operator. D-sched-no-daemon is therefore to be AMENDED after the research P-daemon (DM-research), not yet superseded; until then no daemon starts. (4) ORDER: one current roadmap-order decision replaces the seven ordering notes and the three proposed decisions (D-old-work-behind-core-docs, D-B191-item-ids, D-gate-ratio-noise) are accepted as they stand; done by RH-merge-decisions (superseding records, nothing deleted). (5) README: front door only; each task updates its docs fragment, generated tables included; D-readme-current is superseded by this record and RH-hot-globs rewrites the task globs. (6) PUSH: see D-push-operator-says. (7) ORDER OF ENGINES: SE-actors and SE-ask-core move ahead to priority 11, and B-dt-drafts needs SE-ask-core. (8) MERGES: pure-merge consolidations (export 4, enum fallback 4, skills 3, perf with no-duckdb, MCP surface 4) may be made as superseding records without asking per group; anything that changes meaning comes back as a question. (9) NEW AREAS: all three are to be built: the dependency guard (AG-deps-guard), OpenTelemetry export and ingest (AG-otel), and A2A (AG-a2a), at their queued priorities.

Consequences:
  RH-merge-decisions, RH-hot-globs, RH-authority-matrix and the new P-daemon phase carry it out. The README no longer gets a section per feature. A daemon exists only after P-daemon research and an amending decision.

Alternatives rejected:
  Reviewer: any explicit-model subagent counts; approval always. Upgrade: never write without consent; keep as is. Daemon: strict no long-running process; always ask. Order notes: leave. README: both; generated. Engines: move B-dt-drafts; local stub. Merges: ask per group; do not merge.

Governs: docs/ddflow/decisions/roadmap-review.md, ddflow/services/decisions/**, ddflow/services/subagents/**, ddflow/services/upgrade*.py
```

## D-push-operator-says

```
D-push-operator-says — Push main only when the operator says so
  status accepted · decided by operator

Context:
  P-unify is done and main is about 275 commits ahead of origin.

Decision:
  Operator 2026-10-14 (AskUserQuestion, RH-decide): the block on pushing main stays until the operator explicitly says so; there is no automatic milestone. The condition of D-push-after-acceptance (P-unify acceptance) is met but no longer lifts anything. Ask first as always.

Governs: docs/ddflow/decisions/roadmap-review.md
```

