# OpenHands vs ddflow (research, 2026-10-18; 8 fetches, docs.openhands.dev + GitHub README)

## (a) Feature inventory
Sources: R=https://github.com/All-Hands-AI/OpenHands  O=https://docs.openhands.dev/overview/introduction  L=https://docs.openhands.dev/llms.txt
- Agent Canvas: browser control center for conversations and automations [R,O]. MIT, beta, ~91k stars [R].
- Python SDK (agents, tools, workspaces, conversations) + Agent Server (REST+WebSocket; many agents per machine; OpenAI-compatible endpoints) [R,O, https://docs.openhands.dev/sdk]. TypeScript client [R].
- Runtimes: local (unsandboxed), Docker (single/multi container), remote VM, Kubernetes, OpenHands Cloud [R, /sdk]. Community Sandbox Server = sandbox control plane [O].
- Multi-agent via ACP: runs OpenHands, Claude Code, Codex, Gemini, Pi, OpenCode [R; L: sdk/guides/agent-acp, agent-canvas/acp-agents]. Task tool set for sub-agent delegation [L: sdk/guides/task-tool-set] (content UNVERIFIED).
- Skills: repo/general, keyword-triggered, path-triggered rules, org/user, global/public; creating, monitoring and improving skills pages [L: overview/skills/*].
- Condenser: LLMSummarizingCondenser (RollingCondenser base); triggers past max_context_length events, keeps first keep_first events [https://docs.openhands.dev/sdk/guides/context-condenser.md].
- Security: analyzers (LLM, Pattern regex, PolicyRail composed commands, Ensemble=max risk); risk LOW/MEDIUM/HIGH/UNKNOWN; policies AlwaysConfirm/NeverConfirm/ConfirmRisky; rejection feedback goes back to agent [.../sdk/guides/security.md].
- Critic (experimental): 0-1 predicted-success score; below threshold (default 0.6) auto follow-up prompt, until max iterations [.../sdk/guides/critic.md].
- Stuck detector [L: sdk/guides/agent-stuck-detector] (details UNVERIFIED). Hooks [L: sdk/guides/hooks] (UNVERIFIED details).
- Integrations: GitHub `@openhands` mention or `openhands` label on issue -> comments, works, may open PR; PR mention for review/updates [https://docs.openhands.dev/openhands/usage/cloud/github-installation]. GitLab, Slack, Linear, Notion listed [O, L].
- Automations: scheduled and event(webhook)-based, via Automation Server [R,L: usage/automations/*].
- Cloud/Enterprise: hosted execution, access controls, usage reporting, budgets, roles/permissions [O; L]. Specific RBAC/SSO detail UNVERIFIED.
- Evaluation harness / SWE-bench: NOT confirmed in fetched pages (UNVERIFIED). MCP: SDK mentions MCP integration [/sdk]; event stream: UNVERIFIED by name.

## (b) Side by side
| Aspect | OpenHands | ddflow | Note |
|---|---|---|---|
| Layer | agent runtime, sandbox, UI, server | queue, gates, orchestration | complementary |
| Work model | one conversation per task; issue -> PR | phases/tasks, needs, globs, leases, worktrees | ddflow richer |
| Isolation | Docker/VM/K8s sandbox | git worktree + lease | gap: no OS sandbox |
| Multi-agent | ACP lanes, task tool | P-acp, P-fleet, P-spawn planned | parity planned |
| Quality | critic score + retry loop (single model) | gate pipeline, cross-family reviewers, enforced | ddflow stronger |
| Risk control | action-level analyzer + confirm policy | P-safeguards planned; gate rules | partial gap |
| Context mgmt | condenser | P-governor ladders, memory/lessons | different focus (session vs project) |
| Knowledge | skills (keyword/path triggered) | decisions/rules/lessons by globs, recall | ddflow has glob-scoped; keyword trigger is minor |
| Triggers | @mention/label, schedules, webhooks | P-autopilot, P-remote planned; pr_sync | gap: issue-triggered intake |
| Stuck handling | stuck detector | health watch | similar |
| Multi-user | orgs, roles, budgets | P-governor cost envelopes | planned |
| History | event stream | event log (source of truth) | same idea |
Integration: ddflow as orchestrator driving OpenHands as ACP/agent-server lane (REST+WS) in a Docker sandbox; OpenHands Automations/mention could create ddflow tasks.

## (c) Gap ideas (ranked)
1. Agent-server lane backend (OpenHands REST/WS as a harness descriptor). What: drive a remote OpenHands agent server per claim. Why: sandboxed, remote lanes. Design: new harness descriptor under P-acp registry; claim -> create conversation with worktree mounted; heartbeat from server events; gates unchanged. Cost M. Risk: API drift (beta). Deps: P-acp lane driver.
2. Sandboxed execution profile per lane. What: run the lane in a container with only the worktree mounted. Why: containment for unattended autopilot. Design: lane launcher option `sandbox=docker`, globs -> mount allowlist, network policy in config; record in lease event. Cost M-L. Risk: tool/auth passthrough. Deps: P-agent-parity launcher, P-safeguards.
3. Risk-classified action confirmation (analyzer ensemble, max risk). Design: reuse permission broker (P-acp) + safeguard rules as pattern analyzer; classes LOW/MED/HIGH/UNKNOWN logged as events; HIGH -> operator or cross-family LLM check; rejection reason fed back. Cost M. Risk: false positives stall autopilot. Deps: P-safeguards, P-acp broker.
4. Issue/PR mention-label intake. Design: external_sync/pr_sync already poll; add label `ddflow` -> task_add with dedupe via `similar`; result PR comment from gate summary. Cost S-M. Risk: untrusted issue text -> prompt injection; mark trust=unknown. Deps: P-autopilot, P-remote.
5. Score-gated retry loop (critic). Design: optional gate reporting 0-1 score from reviewer; below threshold auto re-prompt up to N, then escalate; ddflow already has review triage, so only the bounded loop is new. Cost S. Risk: score gaming; keep as advisory next to enforced gates. Deps: review gate.
6. Condenser-style context compaction hook. Design: governor safe-point action summarizing session into a session note keeping first-N pinned (brief, claim). Cost M. Risk: lossy; log. Deps: P-governor.
7. Skill effectiveness monitoring (skills page "monitoring and improving"). Design: count which rules/lessons/skills were delivered vs task outcome (gate failures), surface in P-lessons curation. Cost S-M. Risk: weak causal signal. Deps: P-lessons.
8. Keyword-triggered knowledge delivery. Design: rules/lessons get optional `keywords` besides globs, matched against prompt in brief/recall. Cost S. Risk: noise. Deps: none.

## (d) Rejected
- Own GUI/Agent Canvas: out of scope; P-control-planes covers others.
- Own LLM agent loop, tool set, browser tool: ddflow stays orchestrator.
- Single-model critic as a gate: violates cross-family rule.
- Evaluation harness (SWE-bench): UNVERIFIED relevance; ddflow measures via P-agent-parity.
- Enterprise SSO/RBAC: premature.

## (e) Proposed tasks (not filed)
- R-oh-lane: "OpenHands agent-server lane descriptor"; needs P-acp driver task; globs src/ddflow/lanes/**, docs/**.
- R-oh-sandbox: "Container sandbox profile for lanes"; needs R-oh-lane or agent-parity launcher; globs src/ddflow/launch/**.
- R-oh-risk: "Action risk classes and confirm policy"; needs P-safeguards policy task, P-acp broker; globs src/ddflow/safeguards/**.
- R-oh-intake: "Label/mention issue intake with trust=unknown"; needs external_sync; globs src/ddflow/external/**.
- R-oh-retry: "Score-gated bounded retry advisory"; needs review gate; globs src/ddflow/gates/**.
- R-oh-keywords: "Keyword triggers for rules/lessons"; globs src/ddflow/rules/**, src/ddflow/lessons/**.
(Paths are guesses; UNVERIFIED against repo layout.)
