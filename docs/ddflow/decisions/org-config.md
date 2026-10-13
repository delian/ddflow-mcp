# Central configuration: sources, layer order, enforced policy

Decision document for task `B-org-decide`, filed by `P-decisions-docs.write`. It restates the decision records below; the records in the event log are the source of truth (`ddflow decision show <id>`).

## D-org-config: Central configuration: reviewed lock-bump updates (signed enforced policy may auto-apply when the source opts in); the project may name sources and signatures are optional; fail open with a warning when policy cannot load; the remaining design defaults accepted

status **accepted** · decided by operator · decided by opus-lead

**Decision.** Operator 2026-10-06 (B-org-decide answers): (1) a newer company version is detected and shown in the brief; it is applied through an update task or PR that bumps the committed .ddflow/org.lock; exception: SIGNED enforced policy may apply immediately when the source sets auto_apply = enforced, recorded as an event; (2) a project's own config may declare company sources (no machine allowlist required); signatures are optional (verified and shown when present; auto-apply of enforced policy still requires a valid signature); (3) when enforced policy cannot be loaded and no verified copy is cached, ddflow continues with shipped defaults and a loud warning in brief and doctor (fail open); (4) accepted: sources in project, ~/.config/ddflow/org.toml or /etc/ddflow/org.toml; layers shipped < company defaults < project < local < CLI/env < company enforced; enforced kinds pin/floor/ceiling/require/allow with enforce|audit; commands, hooks and MCP entries from a company source stay inert until digest-approved; auto-discovery of <org>/ddflow-config only proposes.

Governs: `ddflow/services/org/**`
