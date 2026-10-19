# Codegen / ClickUp vs ddflow

Interpretation: (1) Codegen = codegen.com hosted AI software-engineer platform; (2) ClickUp = project-management tool with AI (Brain, Super Agents). 4 web fetches used (1 failed 404).
Sources: S1 https://docs.codegen.com ; S2 https://developer.clickup.com/ ; S3 https://clickup.com/ai ; S4 developer.clickup.com/docs/index = 404.
ddflow check: `ddflow similar` for tracker status mapping, custom fields, estimates/workload, recurring/templates: no match >=0.5 (no existing records). CP-intake (open, needs CP-feed-push) = push intake over feed idempotency keys, mapped fields as data, imported text fenced trust=imported. `ddflow external` has only `sync` (observes sibling-repo items). Phases present: P-labels (waves/themes/release trains), P-fleet, P-control-planes, P-continuous-improvement. No goals/roadmap phase matched grep except the listed ones.

## (a) Feature inventory
### Codegen (S1)
- Enterprise deployment of AI code agents: "sandboxes, integrations, telemetry" building blocks.
- Agent tasks: analysis, feature implementation, bug fixes, tests, docs, PR review, auto test-fixing, ticket management.
- Integrations: GitHub (PRs, branches, commits), Slack, Linear, Jira, ClickUp, Monday.com, Figma, databases; custom MCP tools; Claude Code provisioning.
- Isolated sandboxes; Python SDK and API; telemetry/monitoring; SOC 2 Type I/II.
- UNVERIFIED: ticket-assignment-to-PR trigger flow, run object model/statuses, pricing, per-run logs (only inferred from the overview).
### ClickUp
- API v2/v3, OAuth + personal token (S2): tasks (create/update/filter/merge), custom fields at hierarchy levels, dependencies (blocking/waiting), goals and key results, time tracking entries, comments, chat, webhooks, views (List/Board/Calendar/Timeline/Gantt), attachments, Spaces/Folders/Lists, audit logs, roles, rate limits by plan, MCP server for AI assistants (S2).
- Brain2 (S3): multi-model access, workspace context across tasks/docs/chat; Super Agents by role (Strategist, Developer, Designer; Slides/Web/Product/Finance/Design/Research); agents run inside task framework carrying "state, owners, and dependencies"; automate recurring workflows.
- UNVERIFIED (not fetched, from general knowledge): statuses per list, sprints, workload view, time estimates, automations, templates, recurring tasks, assigning agents on tasks, Autopilot naming.

## (b) Comparison
Roles: Codegen = hosted ticket-to-PR agent; ClickUp = tracker/PM with AI; ddflow = local-first event-sourced orchestrator (system of record).

| Concept | Codegen | ClickUp | ddflow |
|---|---|---|---|
| Work unit | agent run on a ticket (UNVERIFIED model) | task | task in phase |
| Execution | cloud sandbox | none (agents act on workspace) | lease + git worktree |
| Quality | PR review/checks | none | gate pipeline, cross-family reviewers |
| Memory | telemetry | Brain context | decisions/rules/lessons/research |
| Intake | tickets from Linear/Jira/ClickUp/Slack | n/a | CP-intake (planned) |
| Truth | vendor-hosted | vendor-hosted | committed event log |

| ClickUp | ddflow mapping |
|---|---|
| Status (list-scoped) | task state + gate progress; map open/claimed/gates/merged/completed to configurable status names |
| Custom field | tag / mapped field as data (CP-intake "mapped fields are data"); labels (P-labels) |
| Dependency blocking/waiting | `needs` |
| Sprint | label wave / release train (P-labels), version cut |
| Goal / key result | planned goals; progress from phase done/total |
| Folder/List/Space | project / phase |
| Time estimate, workload | none; could derive from history |
| Automation | jobs / hooks / autopilot (planned) |
| Assigned agent | lease holder / claim identity |
| Webhooks | CP-feed-push |
Direction: ddflow as system of record, ClickUp a derived view (exporter), with limited inbound (comments, new tasks) via CP-intake.

## (c) Ranked gaps
1. ClickUp view exporter + status mapping (CP-exporters). What: push ddflow tasks as ClickUp tasks, state->status map, needs->dependencies, phase->list, labels->tags. Why: PM visibility without making ClickUp truth. Design: exporter record kind with mapping table (data, per project/org); idempotency key = task id stored in a custom field; reuse external sync + feed. Cost M; risk API plan limits/rate limits, drift; deps CP-feed-push, CP-exporters.
2. Two-way intake from ClickUp (CP-intake). What: tasks entering a ClickUp status become ddflow tasks; comments become notes. Why: operators plan in ClickUp. Design: importer record kind, fenced trust=imported text, webhook via feed; conflicts resolved: ddflow wins on state, tracker wins on title/description until claimed. Cost M; risk prompt injection, loops; deps CP-intake.
3. Ticket-to-PR run model (Codegen). What: a ticket trigger starts claim->worktree->gates->PR automatically with status comment-back. Why: Codegen's core loop. Design: intake item + autopilot job + PR integration already planned (pr_sync); status comments via exporter. Cost M; risk unattended spend; deps 2, autopilot, cost envelopes (P-safeguards).
4. Custom fields on records. What: typed user-defined fields (number, enum, date) on tasks. Why: ClickUp parity, estimates, priorities. Design: field definitions as a record kind (RC1-RC12); values logged as events; searchable; shown in list/export. Cost M; risk schema sprawl vs tags; deps shared record kinds.
5. Estimates and workload view. What: estimate field + actual (lease/gate time from history) + per-agent load. Why: planning, ClickUp workload parity. Design: built on gap 4 and `history`; report only. Cost S-M; risk poor estimate accuracy; deps 4.
6. Sprints as release trains. What: time-boxed label with start/end, burndown from phase counts. Design: P-labels wave + version cut; no new engine. Cost S; deps P-labels.
7. Recurring tasks and task templates. Design: template record kind + job (cadence exists: `cadence`) that adds tasks. Cost S; risk duplicate floods (use dupes/similar). deps shared record kinds.
8. Assigned-agent semantics. What: assign a task to a named agent/role before claim; routing in `next`. Design: identity (ddflow_identify) + label/field `assignee`; enforce in claim. Cost S; risk deadlock if agent absent; deps 4 or labels.

## (d) Rejected
- Making ClickUp the system of record (breaks local-first, replay).
- Hosted sandboxes (worktrees suffice; fleet covers remote).
- Brain-style opaque workspace AI memory (records already searchable/recallable).
- Chat, docs, whiteboard, Gantt UI replication.
- Per-vendor bespoke code: do one mapping engine.

## (e) Proposed tasks (not filed)
- CP-clickup-exporter: ClickUp view exporter with status/field mapping; needs CP-exporters, CP-feed-push; globs src/ddflow/exporters/**.
- CP-clickup-intake: ClickUp importer + webhook; needs CP-intake; globs src/ddflow/intake/**.
- X-ticket-to-pr: ticket-triggered run with comment-back; needs CP-clickup-intake, autopilot, P-safeguards cost envelope; globs src/ddflow/autopilot/**.
- X-custom-fields: typed field record kind; needs P-shared-engines record kinds; globs src/ddflow/records/**.
- X-estimates-workload: estimate field + workload report; needs X-custom-fields; globs src/ddflow/reports/**.
- X-sprint-trains: sprint labels with dates and burndown; needs P-labels.
- X-templates-recurring: templates + cadence-driven task adds; needs shared record kinds.
- X-assignee: assignment routing in next/claim; needs X-custom-fields or P-labels.
(Globs are guesses; verify paths before filing.)
