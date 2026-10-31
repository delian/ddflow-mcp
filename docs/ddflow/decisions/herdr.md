# Herdr integration (proposal)

Status: **DECIDED 2026-10-24** (operator, decision `D-herdr-answers`; answers table below). Originally a proposal pending HD-decide. Research: `docs/ddflow/research/herdr.md`.

## Proposal D-herdr

Herdr (herdr.dev, Apache-2.0 terminal runtime for coding agents) is integrated as one more lane backend under the existing control-plane decisions, not as a new architecture:

1. A Herdr backend (`DRV-herdr-lane`) implements the Lane contract of `DRV-lane-interface` / `CP-backend-iface`: start through `agent start` in a pane, observe through `events.subscribe`, steer through `agent prompt`, stop and clean up. Missing capability or missing Herdr is `unavailable`, never emulated.
2. D-control-planes applies unchanged: templates are operator-authored, trusted bots may start from approved templates, ddflow adopts Herdr worktrees (owner=external) and never deletes them.
3. Completion is a ddflow event (gate, complete), never Herdr `idle`/`done`. A prompt timeout is not proof of not sent: read before retrying.
4. A blocked agent becomes an operator-queue item; the answer reaches the pane only through the approval flow (`agent send-keys`), never automatically. Screen text is untrusted data.
5. ddflow works unchanged inside a Herdr pane; it only detects `HERDR_*` env for identity and doctor, and its hooks coexist with `herdr integration install` hooks.
6. Each lane gets its own Herdr named session (`HERDR_SESSION`) and socket; Herdr is never started or installed by ddflow without the operator's consent.

Alternatives rejected: treat Herdr as a second ledger of agents (duplicates DX-fleet-registry); a Herdr-specific scheduler (use P-swarm); read Herdr `agent read` as the transcript (screen snapshot only); ddflow bundling Herdr.

## Questions for the lead (defaults in bold; answers in the next section)

| Id | Question | Default |
|---|---|---|
| Q1 | Priority of Herdr relative to Paseo and ACP (P-acp)? | **After Paseo and ACP; only HD-probe, HD-env-detect and HD-hooks-coexist before the swarm pilot** |
| Q2 | May ddflow install Herdr (curl installer) for the probe on this host? | **No: probe in a scratch directory from a release binary the operator approves, or mark HD-probe unavailable** |
| Q3 | May ddflow start agents inside Herdr (bot start from templates)? | **Yes, same rule as D-control-planes: operator templates only, every start an event** |
| Q4 | Build a Herdr plugin (operator queue view) or docs-only recipe? | **Docs-only recipe (HD-host-recipe); plugin later** |
| Q5 | Blocked-agent approvals: answer from the chat channel or only from the terminal? | **Same policy as P-remote-approvals; no auto-answer ever** |
| Q6 | Trust level of Herdr saved SSH machines as lanes? | **Untrusted remote lanes (SW-remote-trust) until conformance passes** |

## Operator's answers (D-herdr-answers, 2026-10-24)

Decision `D-herdr-answers`, status accepted, decided by operator. Q4 departs from the default; the rest are as default or accepted.

| Id | Answer | The decision's words |
|---|---|---|
| Q1 | default | "priority after Paseo and ACP" |
| Q2 | default | "probe Herdr with a scratch release binary the operator approves (no system install, no keys)" |
| Q3 | yes | "bots and the scheduler may start agents inside Herdr from operator-approved templates only" |
| Q4 | **changed** | "the operator chose to ALSO BUILD A HERDR INTEGRATION PLUGIN, not a docs-only hosting recipe (the recommendation was docs-only): add HD-plugin" |
| Q5 | accepted | "approvals for agents running inside Herdr follow the P-remote-approvals policy and are never auto-answered; a blocked agent reaches the operator queue" |
| Q6 | accepted | "remote Herdr machines saved over SSH are untrusted lanes until the swarm conformance run passes" |

Consequences recorded with the decision: a plugin widens the coupling to Herdr's 0.x API: it must live behind the lane driver (DRV-herdr-lane, DRV-plugin-sdk), be optional, pinned to a tested Herdr version range with a drift check in doctor, and installed only with operator consent and a diff (as for Kilo/OpenCode plugins). The task `HD-plugin` exists for it (the HD-host-recipe docs recipe stays in the queue).

## Queue

Phase `P-herdr`: DX-herdr-research (this), HD-decide, HD-probe, HD-env-detect, HD-foreign-ids, HD-status-map, DRV-herdr-lane, HD-adopt-worktree, HD-blocked-approvals, HD-hooks-coexist, HD-security, HD-swarm-conformance, HD-host-recipe, HD-plugin (added by D-herdr-answers Q4). Waves: 1 research/decide/probe; 2 env-detect, foreign-ids, status-map, hooks-coexist; 3 driver, adopt, blocked-approvals; 4 security, conformance, host recipe.

Governs: `docs/ddflow/decisions/herdr.md`, `ddflow/services/lane_herdr.py`
