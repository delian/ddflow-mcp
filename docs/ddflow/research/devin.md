# Devin vs ddflow (research, 2026-10-18)

Sources: docs.devin.ai pages fetched this session (URLs inline). UNVERIFIED = not confirmed in a fetched page.

## (a) Feature inventory
- Hosted autonomous agent; own Linux VM per session with shell, IDE, browser; aimed at tasks of ~3 hours. https://docs.devin.ai/ , https://docs.devin.ai/onboard-devin/repo-setup
- Environment = snapshot: frozen bootable image from a config (declarative YAML "blueprints"); each session boots a fresh copy, changes do not persist; new snapshot builds automatically when config changes; Devin can set it up itself. Secrets stored encrypted in blueprint editor, injected as env vars. https://docs.devin.ai/onboard-devin/repo-setup ; blueprints https://docs.devin.ai/onboard-devin/environment/blueprints.md
- Playbooks: shareable reusable prompts; sections Procedure / Specifications (postconditions) / Advice / Forbidden actions / Required from user; `!macro` ids unique per org; org and enterprise level. https://docs.devin.ai/product-guides/creating-playbooks
- Knowledge: items with a trigger description, retrieved on relevance; auto-suggested from chat feedback (edit/dismiss); scopes org/enterprise/personal; pin to repos; folders; promote org->enterprise. NOTE: being deprecated in favour of Skills within Plugins, auto-migrated. https://docs.devin.ai/product-guides/knowledge
- Skills and Plugins, team marketplace, Devin CLI (with /handoff), MCP servers + marketplace, DeepWiki MCP. Index: https://docs.devin.ai/llms.txt
- DeepWiki repo wikis, Ask Devin, Devin Review (pages listed in llms.txt; contents UNVERIFIED beyond titles).
- Scheduled Sessions and Automations (listed in llms.txt; contents UNVERIFIED).
- Integrations named: Slack, Teams, Linear, Jira, PR review (https://docs.devin.ai/); GitHub UNVERIFIED in fetched text.
- API v3: org scope (sessions, knowledge, playbooks, secrets) and enterprise scope (analytics, audit logs, users); service users `cog_`-prefixed with RBAC or PATs; `create_as_user_id` to attribute sessions; v1/v2 legacy. https://docs.devin.ai/api-reference/overview
- Enterprise: orgs, custom roles/RBAC, SSO, IP allow lists, AI guardrails, dedicated/private networking, environment rollout health, OIDC cloud auth, VPN. Titles only, via llms.txt.
- Billing: ACU-based; pages exist (admin/billing). Prices/limits UNVERIFIED.

## (b) Side by side
| Axis | Devin | ddflow |
|---|---|---|
| Nature | Hosted agent + VM (the worker) | Local-first orchestrator and gate layer (not a worker) |
| Work unit | Session (free-form prompt, optional playbook) | Task with needs, globs, lease, worktree, gate pipeline |
| Environment | Snapshot/blueprint VM, rebuilt on config change | Git worktree on the operator's machine; no env spec |
| Reusable procedure | Playbook (+macros) | Flow/workflow pipelines, driver docs; no per-task procedure template found by `ddflow similar` |
| Memory | Knowledge (trigger-retrieved, suggested), moving to Skills | Lessons, rules, decisions, research, recall/brief; scoped, matched; P-lessons, P-knowledge-hub, P-lesson-packs planned |
| Verification | Devin Review (PR review) | Gate pipeline, cross-family reviewers enforced, regression-test rule |
| Parallelism | Many sessions, managed Devins | Leases, merge queue, adaptive parallelism; P-fleet planned |
| Cost | ACUs | Meters/spend ledger/pacing (P-governor, P-agent-parity.meters) |
| Secrets | Encrypted store injected as env | Tokens never in repo/log; auth stays with agent |
| Control surface | Web, Slack, Teams, API, CLI | CLI + MCP |
| Audit | Enterprise audit logs | Event log in git (source of truth) |
Complements: ddflow drives Devin via API as a guarded lane backend (already in P-platform-mcp scope); ddflow gates review Devin's PR; ddflow lessons/rules/decisions export to Devin Knowledge/Skills/Playbooks.

## (a2) Added facts
- Scheduled sessions: visual presets or cron (UTC), playbook attached per run, email on always/failure(default)/never, optional Slack; now legacy, new work is an "automation" with schedule/event triggers and conditions. ACU/limits not documented there (UNVERIFIED). https://docs.devin.ai/product-guides/scheduled-sessions.md
- Devin Review: PR review with diff organisation, bugs with confidence, CWE security findings; triggers: manual URL, `/devin review` PR comment, auto-review on open/commit/ready; per-PR ACU spend cap (enterprise), custom review rules by file glob, Auto-Fix proposals. https://docs.devin.ai/work-with-devin/devin-review.md
- DeepWiki: generated architecture docs and diagrams per repo; Ask Devin grounded on it; `.devin/wiki.json` with repo_notes (<=100, 10k chars) and pages (<=30, enterprise 80); low effort free, medium ~5-10 ACU, high ~20-40 ACU (as quoted by the page; treat as UNVERIFIED pricing). https://docs.devin.ai/work-with-devin/deepwiki.md

## (c) Gaps, ranked (ddflow `similar` found no existing task/lesson for any of these; ones overlapping planned phases noted)
1. Task procedure templates (Playbook analogue). What: reusable, named procedure with Procedure / Postconditions / Advice / Forbidden / Required-inputs sections, attached to a task at add time (`--procedure !name`). Why: tasks currently carry free text; postconditions map onto gates and make "done" checkable. Design: `procedure` record type in event log (like rules), fields as above; `task add --procedure`; brief injects the procedure into the claim prompt; postconditions can seed a gate checklist; export as Devin playbook/Claude skill. Cost M. Risk: duplicates flows/workflows; keep it per-task, not per-pipeline. Deps: P-lesson-packs (pack distribution), P-platform-mcp (export).
2. Per-task budget cap with auto-pause (Devin Review per-PR ACU cap). What: a spend/time/token ceiling on one task or one gate run. Why: runaway sessions. Design: `budget` field on task; meters (P-governor) check at heartbeat; breach blocks the lease and raises an attention item. Cost S-M. Risk: meters imprecise per vendor. Deps: P-governor, P-agent-parity.meters.
3. Lesson suggestion from sessions (Knowledge suggestions). What: after a session/gate failure, propose draft lessons for operator accept/edit/dismiss. Why: lessons are only recorded when an agent remembers. Design: at session_end and review-triage, a suggester drafts lessons from failed gates and corrections, stored as `suggested` state; brief shows count; accept = lesson add. Cost M. Risk: noise; require dedupe via `similar`. Deps: P-lessons.
4. Trigger-described retrieval for lessons/rules (Knowledge trigger description + repo pinning). Likely already near P-lessons scoping; verify before filing; only add a "when relevant" trigger text field if missing.
5. Reproducible environment spec (blueprint/snapshot). What: declarative per-project env file (setup commands, env names, secret names not values) used to build worktrees and to hand to hosted backends. Why: worktrees fail on missing deps; Devin calls this highest-leverage. Design: `[env]` in config with setup/verify steps, hash recorded in event log at claim, doctor checks drift; no VM building. Cost M. Risk: scope creep into container tooling; keep to commands plus hash. Deps: none; helps P-platform-mcp.
6. Repo context index for agents (DeepWiki / wiki.json). What: generated module map plus curated repo notes injected into brief. Why: cheaper orientation. Design: reuse codeguide-mcp companion; `ddflow brief` cites it; no own generator. Cost S if companion-based. Risk: staleness; overlaps companions. Deps: companions.
7. Scheduled task runs. What: cron-bound task templates (e.g. weekly dep bump) creating tasks from a procedure. Design: job_add already exists (ddflow_job_*) so verify; add "create task from procedure on schedule". Cost S. Deps: gap 1, jobs.
8. Hosted-lane guard rules: `create_as_user_id` style attribution, service-user tokens, per-PR cap mirrored. Fold into P-platform-mcp adapter tasks, not a new phase.

## (d) Rejected
- Own hosted VM/snapshot builder: out of ddflow's local-first identity.
- Own PR-review product: the cross-family reviewer gate already covers it; only borrow the confidence/CWE output fields in reviewer prompts.
- Enterprise RBAC/SSO/IP lists/guardrails: operator is local; revisit only for control-plane tokens.
- Own wiki generator: use companions.
- Knowledge as separate store: Knowledge itself is deprecated into Skills/Plugins; map ddflow lessons to skills instead.

## (e) Proposed tasks (not filed)
- G-procedure-record: "Procedure records (playbook analogue)": needs P-lesson-packs? no; needs none; globs src/ddflow/records/**, src/ddflow/cli/task*.py, docs/**.
- G-procedure-export: "Export procedures/lessons to Devin playbooks and Skills": needs G-procedure-record, P-platform-mcp adapters; globs src/ddflow/platform/**.
- G-task-budget: "Per-task spend/time ceiling with pause": needs P-governor meters; globs src/ddflow/governor/**, src/ddflow/lease*.py.
- G-lesson-suggest: "Suggest draft lessons at session end": needs P-lessons store; globs src/ddflow/lessons/**, src/ddflow/session*.py.
- G-env-spec: "Project [env] spec, hash at claim, doctor drift check": needs none; globs src/ddflow/config*.py, src/ddflow/doctor/**.
- G-brief-repo-index: "Brief cites companion repo index": needs companions; globs src/ddflow/brief*.py.
- G-scheduled-procedure: "Scheduled task creation from a procedure": needs G-procedure-record; globs src/ddflow/jobs/**.
(All globs are guesses; confirm paths before filing.)

## Not verified
Pricing/ACU rates, GitHub/Linear/Jira integration details, Automations, parallel/managed Devins, Ask Devin page, enterprise features beyond titles, MCP API specifics, Cognition changelog (not fetched).
