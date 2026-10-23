# Queue holds: operator decisions, 2026-10-23

Source: `docs/ddflow/research/queue-review-2026-10-23.md`, question DX-queue-holds-decide.
The operator answered the three questions in one batch on 2026-10-23.

## Measured baseline

- Critical path: 37 waves (set by order decisions, not technical need).
- The docs-wave hold (D-old-work-behind-core-docs-accepted): 29 phases and 44 tasks wait for the four docs phases (P-doc-templates, P-docs-generic, P-roadmap, P-docs-search), longest chain 9 deep. 896 of 1581 open tasks (57%) have it among their prerequisites.
- Lifting that hold from every phase would take the critical path from 37 to 30 and total depth from 18148 to 12247 (-33%), at the cost of about 60 tasks ready at once competing for slots.
- P-agent-memory and P-agent-compat need the whole of P-decisions and P-org-config (29 tasks, 17-deep chain). Dropping both needs would take the critical path from 37 to 29; narrowing them gives a figure between.

## 1. Docs-wave hold: kept as is

Decision: D-old-work-behind-core-docs-accepted stays unchanged, including its -r2 exceptions.
Consequence: the critical path stays 37; 896 of 1581 open tasks stay behind the docs wave. The 30-wave option (lift for phases without document work) was declined.

## 2. P-agent-memory and P-agent-compat whole-phase needs: left

Decision: the whole-phase needs on P-decisions and P-org-config stay. The narrowing (to B-dec-model and the org config loader) was declined, as was dropping the needs (37 to 29).
Consequence: the critical path stays 37; the narrowing that would have given 29 is not taken.

## 3. B-gap-spend, B-gap-command-trust, B-gap-checks-publish: released

Decision: these P-gaps core tasks no longer wait for the P-decisions and P-org-config phases, consistent with D-phase-order-gaps-first. The needs were removed with `ddflow update --remove-needs`; B-gap-command-trust is a research-first security question that needs neither.
Consequence: the three tasks can start now; they no longer count toward the P-decisions / P-org-config chains.
