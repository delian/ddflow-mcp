# Session tree, transfer pack, native stores, native lane status

Decision document for task `B-sc-decide`, filed by `P-decisions-docs.write`. It restates the decision records below; the records in the event log are the source of truth (`ddflow decision show <id>`).

## D-session-context: Session context: tree fields, on-demand transfer, native stores, native lane status ON by default

status **accepted** · decided by operator · decided by opus-lead

**Context.** Operator answered the B-sc-decide questions on 2026-10-11; question 6 default flipped from off to auto.

**Decision.** (1) Session tree = additive optional fields parent, role, goal, harness_session on session.started plus a derived 'ddflow session tree'; no new event kind. (2) Transfer = on-demand FENCED rendering of the latest session.handoff plus a small session.transferred event; a file is written only with --file, under git-ignored .ddflow/local/transfers/. (3) Native stores: Claude Code only, read-only; VS Code/Copilot/Codex/Cursor only after B-am-harness-probe shows a stable documented format. ddflow code never calls remote-session MCP tools (create_session, send_message, list_events, get_session); a message is never approval or consent; list_events output is never copied into the log or a handoff unredacted. (4) Native lane status: [lanes] native_status defaults to 'auto' (ON) -- OPERATOR OVERRODE the recommended default 'off'. It stays read-only, reports 'unavailable' (with reason) when the CLI is missing, times out or fields differ, never guesses, and is disable-able with native_status = off (off spawns nothing). Generated VS Code/Copilot files (agents, prompts, hooks) are managed-marked and never overwrite a foreign file (B-sub-sync, B-adopt-agent-commands, B-hx-hook-writers).

**Consequences.** native_status=auto spawns one bounded subprocess per harness on 'ddflow lanes'; every failure mode must degrade to 'unavailable'. Docs and tests must pin auto as the default.

**Alternatives rejected.** New session event kind; committed transfer file per transfer; building on remote-session MCP tools; native_status default off; reading VS Code/Copilot/Codex/Cursor stores before a probe.

Governs: `docs/ddflow/decisions/session-context.md`, `ddflow/services/nativesessions.py`, `ddflow/config_sections/lanes.py`, `ddflow/services/session_tree.py`, `ddflow/services/transfer.py`, `ddflow/core/handlers/sessions.py`, `docs/ddflow/session-context.md`
