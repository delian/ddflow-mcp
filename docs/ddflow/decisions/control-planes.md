# Control-plane integrations

Decision document for the task `CP-decide`. It restates the decision record below; the record in the event log is the source of truth (`ddflow decision show D-control-planes`).

## D-control-planes: Control planes: Paseo first, ddflow adopts foreign worktrees and never deletes them, trusted bots may start agents from operator templates

status **accepted** · decided by operator · recorded by hy-commit-events

**Context.** Operator 2026-10-13 asked how ddflow integrates with and is managed through Paseo, AgentOS, Harness Remote, Nimbalyst, Orca, Emdash; note R-control-planes.

**Decision.** Operator 2026-10-13 (AskUserQuestion, CP-decide): (1) Order: Paseo first, then the AgentOS, Emdash and Harness Remote kits, then Orca and Nimbalyst. (2) Worktrees: ddflow adopts foreign worktrees (claim --adopt, owner=external), never deletes them, and may create them for a plane through a hook (WorktreeCreate for Claude Remote Control). (3) Starting and steering agents through backends: TRUSTED BOTS MAY START AGENTS TOO, from operator-approved templates (the recommendation was operator-only); the templates stay operator-authored, a bot cannot create or edit one, and every start is an event with the bot's identity. Tokens are scoped per item and per lane, additive, revocable, loopback by default; no relay is built (document SSH, Tailscale or the Paseo relay). (4) Remaining defaults accepted: the ACP wrapper in the first wave after the facts probes; the Paseo plugin last; competing attempts a first-class feature; tracker intake webhooks later; Nimbalyst a one-way mirror that never accepts status writes; a missing control plane is unavailable, never passed; unverified facts are probed, not assumed.

**Consequences.** CP-backend-iface and CP-scoped-tokens must let a bot role start lanes only from approved templates and cap the number of starts; CP-security tests a bot trying to start from an unapproved template or edit one.

**Alternatives rejected.** Kits for all planes first; only Paseo; ddflow always creates the worktree; planes own worktrees; read-only integrations only.

Governs: `docs/ddflow/decisions/control-planes.md`, `integrations/**`
