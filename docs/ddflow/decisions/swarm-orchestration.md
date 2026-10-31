# Swarm orchestration: decision (state: DECIDED 2026-10-24 by D-swarm-answers; proposal text kept below)

Research: docs/ddflow/research/swarm-orchestration.md (landscape, algorithms, sharing, remote trust, hooks).
Phase: P-swarm. Task that produced this: DX-swarm-research (folds DX-mixed-fleet-research).
Operator request 2026-10-24 (verbatim in the session log): research how ddflow could manage and orchestrate a swarm of
agents, local or remote, various families, distribute tasks and make the best of it, then make a proposal.

## Operator's answers (D-swarm-answers, 2026-10-24)

Decision `D-swarm-answers`, status accepted, decided by operator. SW-Q2 and SW-Q9 depart from the defaults; the rest are as default or accepted. The proposal text below is kept as written; where it says "default" the answers here govern.

| Id | Answer | The decision's words |
|---|---|---|
| SW-Q1 | default | "Q1 option B (deterministic scheduler with pull lanes; option C LLM orchestration only for read-only research items)" |
| SW-Q2 | **changed** | "the scheduler MAY launch lanes itself from approved templates (not only answer pulls; the recommended 'pull only in v1' is overruled), launching still bound by D-agent-parity consent and operator-approved templates" |
| SW-Q3 | default | "pilot families Claude, opencode or kilo with DeepSeek, Qwen or Kimi" |
| SW-Q4 | default | "target at least 1.8x merged items per hour at no more than 1.3x cost per merged item, rework within baseline plus 5 points" |
| SW-Q5 | default | "hedging only for stragglers and items tagged hard, capped at 10% of fleet spend, first branch to pass the gates merges" |
| SW-Q6 | accepted | "hooks for lane workers are lane-private (ATF-Q2); plugin-style hooks (Kilo, OpenCode) need operator consent and a diff once per template" |
| SW-Q7 | accepted | "an agent without a blocking pre-tool hook may work narrow-glob items with the lease-checking git pre-commit hook (detection at commit, not prevention)" |
| SW-Q8 | accepted | "one remote lane in the final pilot step (ACP over SSH stdio or ddflow serve through SSH or Tailscale; no relay built)" |
| SW-Q9 | accepted, **amended** | "a remote lane never approves, skips a gate or writes the event log directly; it MAY PROPOSE approval requests which only the operator answers (amended from the recommendation); the host records events and runs the gates on the returned branch; token per item and lane, revocable" |
| SW-Q10 | default | "quality ledger from gate outcomes per family, model and kind, 10% exploration for pairs with fewer than 5 samples; unavailable never counts as passed" |

Consequences recorded with the decision: SW-Q2 widens the trust surface: the scheduler's launch step needs the approved-template registry, lane-private homes, budget and limit checks (TE-limit-aware-routing), and an audit event per launch. Task bodies amended with this decision: SW-pull (also launches) and SW-remote-trust (proposals allowed).

## Proposal in one paragraph

Option B: a deterministic scheduler (ddflow code, not an LLM) that answers pulls from idle lanes with the best ready
item for that lane. Hard constraints first (needs, disjoint globs, caps, headroom, trust, reviewer independence,
lane conformance level), then a score (critical path unlocked, capability fit from a quality ledger built from gate
outcomes, cost, expected rework, queue pressure). One writer per item; the event log is the shared blackboard; context
reaches a worker as a tier-0 brief plus on-demand fenced handoff; remote workers are untrusted, work from a bundle,
return a branch, and the host runs the gates. Hooks are lane-private files inside the lane directory (ATF-Q2) with the
lane's own identity, with a lease-checking git pre-commit hook as the fallback for agents with weak or no hooks.
Piloted on a toy phase (3 local lanes of 3 families, then 1 remote) against a measured baseline.

## Reused decisions and items (propose folds, none done here)

D-agent-parity (launch with consent, operator-owned templates), D-acp-r2 (ACP lane driver, auto-allow rule),
D-control-planes (Paseo first, trusted bots, scoped tokens, competing attempts), D-session-context (tree, fenced
transfer, no remote-session MCP calls), D-lock-cycle-selfheal, D-wave-semantics, D-ATF-Q2 (lane-private config),
DRV-lane-interface, DRV-engines, DRV-model-catalog, DRV-plugin-sdk, DSH-lane, TE-limit-aware-routing, TE-auto-resume,
P-batch-gates, P-remote-approvals, P-remote, P-operator-digest, DX-fleet-registry, DX-drain-mode, P-agent-memory,
P-orphan-recovery, MTD-accuracy-eval. Proposed folds: TE-limit-aware-routing becomes the data source of SW-headroom (no
second price table); DX-fleet-registry is the lane store the scheduler reads; competing attempts (D-control-planes) are
the hedge mechanism of SW-hedge.

## Questions for the lead to ask (SW-decide), each with a default

| Id | Question | Default |
|---|---|---|
| SW-Q1 | Which option? A manual lead dispatch, B deterministic scheduler with pull lanes, C LLM-orchestrated swarm | B; C only for read-only research items |
| SW-Q2 | May the scheduler launch lanes itself, or only answer pulls from lanes the operator or lead started? | Answer pulls only in v1; launching stays with operator templates (D-agent-parity) |
| SW-Q3 | Which families join the first pilot? | Claude (lead and one worker), opencode or kilo with DeepSeek, Qwen or Kimi; Gemini, Cursor, Grok later |
| SW-Q4 | Speed and cost target for the pilot | At least 1.8x merged items per hour at no more than 1.3x cost per merged item, rework no worse than baseline plus 5 points |
| SW-Q5 | Hedging: allowed, and the cap on duplicate spend | Allowed only for stragglers and items tagged hard; cap 10% of fleet spend; first branch to pass the gates merges |
| SW-Q6 | Hooks for lane workers: lane-private files (ATF-Q2) or project files? | Lane-private inside the lane directory, listed in the lane record, removed with the lane; plugin-style hooks (Kilo, OpenCode) need operator consent and a diff once per template |
| SW-Q7 | An agent with no blocking pre-tool hook (Grok today): allowed as a worker? | Yes, on items whose globs are narrow, with the lease-checking git pre-commit hook; documented as detection at commit, not prevention |
| SW-Q8 | Remote workers: allowed in v1, and over which transport? | One remote lane in the final pilot step; ACP over SSH stdio or ddflow serve through SSH/Tailscale; no relay built |
| SW-Q9 | Remote trust: may a remote lane ever approve, skip a gate or write the event log directly? | No to all; the host records events and runs the gates on the returned branch; token per item and lane, revocable |
| SW-Q10 | Quality routing data: may the ledger use gate outcomes per (family, model, kind) to steer items, with an exploration share? | Yes; 10% exploration for pairs with fewer than 5 samples; unavailable outcomes never count as passed |

## Tasks (phase P-swarm, filed with this proposal)

| Id | Needs (verified existing ids) | Globs |
|---|---|---|
| SW-decide | none (not in anyone's needs, L-needs-on-decide-tasks) | docs/ddflow/decisions/swarm-orchestration.md |
| SW-baseline | none | ddflow/services/swarm_metrics.py, tests/test_swarm_metrics.py |
| SW-ledger | SW-baseline | ddflow/services/swarm/ledger.py, tests/test_swarm_ledger.py |
| SW-score | SW-ledger, DRV-model-catalog | ddflow/services/swarm/score.py, tests/test_swarm_score.py |
| SW-pull | SW-score, DX-fleet-registry | ddflow/services/swarm/dispatch.py, tests/test_swarm_dispatch.py |
| SW-headroom | SW-score, TE-limit-aware-routing | ddflow/services/swarm/headroom.py, tests/test_swarm_headroom.py |
| SW-conformance | DRV-lane-interface | ddflow/services/swarm/conformance.py, tests/test_lane_conformance.py |
| SW-lane-hooks | SW-conformance | ddflow/services/swarm/lanehooks.py, tests/test_lane_hooks.py |
| SW-git-fallback | none | ddflow/services/swarm/githook.py, tests/test_lane_githook.py |
| SW-hedge | SW-pull | ddflow/services/swarm/hedge.py, tests/test_swarm_hedge.py |
| SW-reassign | SW-pull, TE-auto-resume | ddflow/services/swarm/reassign.py, tests/test_swarm_reassign.py |
| SW-seed-pack | SW-conformance | ddflow/services/swarm/seedpack.py, tests/test_swarm_seedpack.py |
| SW-remote-bundle | SW-seed-pack | ddflow/services/swarm/remote.py, tests/test_swarm_remote.py |
| SW-remote-trust | SW-remote-bundle | ddflow/services/swarm/remote_trust.py, tests/test_swarm_remote_trust.py |
| SW-digest | SW-pull | ddflow/services/swarm/digest.py, tests/test_swarm_digest.py |
| SW-pilot-local | SW-pull, SW-lane-hooks, SW-git-fallback, SW-baseline | docs/ddflow/research/swarm-pilot.md |
| SW-pilot-remote | SW-pilot-local, SW-remote-trust | docs/ddflow/research/swarm-pilot-remote.md |
| SW-docs | SW-pilot-local | docs/ddflow/guides/swarm.md |

The needs graph is acyclic by construction (every edge points to an earlier row). Waves follow D-wave-semantics and are
stamped from the queue, not here.

## What would invalidate the proposal

Parallel speedup near 1 because the host (suite lock, CI queue) is the bottleneck: SW-baseline measures this first and
the rest should stop if so. A family's tool-call accuracy on the lean-core tools too low to follow ddflow rules.
Provider terms or limits that forbid parallel use of one subscription. Mixed-fleet rework above twice the baseline.

## Consequences if accepted

ddflow gains a SCHEDULER and a CONFORMANCE notion; the human lead stops hand-assigning items; lane hooks and seed packs
become generated, removable artefacts; the operator digest explains each dispatch. Nothing changes for a single agent
working alone.

## Alternatives rejected

A alone (no learning, lead is the bottleneck); C as the default (about 15x tokens, non-deterministic fan-out,
conflicts with one writer per item, Cognition critique); contract-net auctions (model tokens spent to learn what the
catalog already knows); speculative execution of dependent writes (rework risk); a shared mutable blackboard other than
the event log; project-level hook files for fleet lanes (shared, wrong identity).
