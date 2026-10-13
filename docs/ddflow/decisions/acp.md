# ACP: Agent Client Protocol

Decision document for the task `AC-decide`. It restates the decision record below; the record in the event log is the source of truth (`ddflow decision show D-acp-r2`).

## D-acp-r2: ACP: client, wrap proxy and a thin ACP agent; a backend beside native ones; auto-allow inside the worktree and lease-covered repo files; steer by capability

status **accepted** · decided by operator · recorded by hy-commit-events · supersedes D-acp

**Context.** Operator 2026-10-13 asked whether ddflow should implement ACP to communicate with and control agents; note R-acp.

**Decision.** Operator 2026-10-13 (AskUserQuestion, AC-decide): (1) Roles: ACP CLIENT (lane driver), the WRAP PROXY (ddflow acp --wrap), AND a THIN ACP AGENT exposing /claim, /next and /status (and /brief) as slash commands; the recommendation was no agent role. Recorded reading: the thin agent has no model of its own, answers those commands from the queue, and every state-changing command goes through the same authorization and approvals as the CLI and MCP. (2) ACP is a generic backend beside the native ones; a per-agent preference knob (native, acp, tmux) with defaults from the research (native for claude, codex and opencode where fidelity matters). (3) Permissions: AUTO-ALLOW the lane's worktree plus the repo files that its LEASE GLOBS cover; never .git, ddflow's .ddflow state and event shards, or other lanes' worktrees; everything else asks through the approvals engine. The operator first chose 'inside the worktree and the repo', and on being asked what that covers confirmed this reading (AskUserQuestion follow-up 2026-10-13): lease-covered repo files, not the whole checkout. Every decision is logged and the policy is a record the operator can narrow. v1 only; no fs or terminal capabilities advertised. (4) Steering by capability (a native extension where present, else cancel and re-prompt); remote HTTP and WebSocket waits for the standard (sockets and ssh-tunnelled stdio meanwhile); the spike comes first; remaining defaults accepted: adapter versions pinned from the registry and recorded, auth stays with the agent's login, a missing or unauthenticated agent is unavailable, the continuation loop reuses the continuation engine.

**Consequences.** A new task AC-agent builds the thin ACP agent; AC-permissions defaults to allow inside the worktree and the lease-covered repo files with the listed exclusions; AC-eval adds fixtures for an auto-allow that would touch .git, .ddflow or another lane's worktree.

**Alternatives rejected.** Client only; no agent role; allow inside the worktree only; fs and terminal capabilities advertised; HTTP and WebSocket transport now.

Governs: `docs/ddflow/decisions/acp.md`, `ddflow/**`
