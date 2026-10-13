# Importing an existing AI setup

Decision document for the task `OB-decide`. It restates the decision record below; the record in the event log is the source of truth (`ddflow decision show D-ai-import`).

## D-ai-import: Importing an existing AI setup: AGENTS.md canonical, a reversible plan applied as a queue item, permissions and hooks managed like the rest, all supported agents in one wave

status **accepted** · decided by operator · recorded by hy-commit-events

**Context.** Operator 2026-10-13 asked how a project's preexisting AI workflow (instructions, rules, skills, agents, commands, hooks, MCP, permissions) is imported and then managed for every supported agent; note R-ai-config-import.

**Decision.** Operator 2026-10-13 (AskUserQuestion, OB-decide): (1) AGENTS.md is the canonical always-on file; pointers first (Claude gets a short CLAUDE.md that imports it), managed-marker copy with a digest second, never a wholesale rewrite of JSON or TOML. (2) The import is a plan applied as a queue item, reversible (backup, one commit, revert); contradictions block adopt until a decision, and a secret or hidden-Unicode hit blocks emit. (3) Permissions, hooks, plugins, MCP servers and the other formerly native-only classes are MANAGED LIKE THE REST (imported and emitted), not native-only; this differs from the recommendation. Recorded reading: the same plan, backup, revert, drift and secret and hidden-Unicode rules apply to them, and a plan that widens an agent's access (a permission, a skill's allowed tools, an MCP server) is flagged in the plan for review; MCP secrets stay references only. (4) ALL supported agents in one wave, not the recommended staged groups; unverified paths are still probed first and a path that stays unverified is not emitted. Remaining defaults as recommended: user-scope files hashed and never copied; absorb of native edits on request.

**Consequences.** OB-native-only changes from pointer-only records to managed records with a widening flag; OB-agents-* run as one wave behind OB-verify-facts and OB-emit-manifest; OB-eval keeps the permission-widening and injected-MCP fixtures.

**Alternatives rejected.** Native-only for permissions and hooks; staged agent groups; scan-only; directly applied import with warnings only.

Governs: `docs/ddflow/decisions/ai-import.md`, `ddflow/**`
