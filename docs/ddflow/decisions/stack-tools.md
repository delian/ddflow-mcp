# Stack probe and assurance tools

Decision document for the task `SC-decide`. It restates the decision record below; the record in the event log is the source of truth (`ddflow decision show D-stack-tools`).

## D-stack-tools: Stack tools: the agent may accept any role of a proposed tool, provers are opt-in verifiers, guards warn with a ratchet

status **accepted** · decided by operator · recorded by hy-commit-events

**Context.** Operator 2026-10-13 asked whether ddflow should probe a stack and propose assurance tools (Creusot, linters, LSP, AST, contracts) as companions; note R-stack-tools.

**Decision.** Operator 2026-10-13 (AskUserQuestion, SC-decide): (1) The probe is deterministic, offline and installs nothing; ddflow prints the install command and never runs it. (2) The AGENT MAY ACCEPT ANY ROLE of a proposed tool (lint, format, types, contracts, verify, mutation, security, LSP, AST, docs, rules), not only low-risk ones; this differs from the recommendation (operator accepts, agent only for low-risk roles). Recorded reading: acceptance is an event with the accepting agent, the tool pinned to a version and its source, the install-safety checks (licence, source allowlist, MCP description hash) still run, an accepted tool can be reverted by the operator, and the operator can narrow the roles an agent may accept by a knob. A declined tool leaves its gate unavailable. (3) Deductive provers (Creusot, Verus, Dafny, SPARK) are opt-in verifiers named by a workflow, not default gates; agent-plus-internet proposals are allowed with citations, trust=agent, verified false, and the operator accepts those. (4) Guards (new suppressions, edits to tool configs, tests or specs alongside the code they check, proof escapes) WARN with a ratchet that only goes down; block is a per-project setting. (5) LSP and AST MCP servers are advisory only and measured before they are proposed by default; first catalogue stacks Python, Rust, TypeScript/JavaScript, Go, shell/Docker/YAML/IaC; Serena's GPL-3.0 core runs as a separate process; new MCP servers get their tool descriptions hashed at accept time; all workflows name tools from the one catalogue.

**Consequences.** SC-propose lets an agent accept any role with the safety checks; agent-proposed catalogue additions still need the operator; SC-install-safety and SC-guards stay mandatory; SC-eval keeps the poisoned-description and curl-bash fixtures.

**Alternatives rejected.** Operator accepts everything; agent accepts low-risk roles only; provers as default gates; guards blocking; LSP and AST proposed by default.

Governs: `docs/ddflow/decisions/stack-tools.md`, `ddflow/**`
