# Accrete fit: recommended defaults 1-5 accepted (generic seams only, ddflow as source first, egress door, imported answers fenced, no graph store)

Decision document for the task `ACF-decide`. It restates the decision record below; the record in the event log is the source of truth (`ddflow decision show D-ACF-answers`).

## D-ACF-answers: Accrete fit: recommended defaults 1-5 accepted (generic seams only, ddflow as source first, egress door, imported answers fenced, no graph store)

status **accepted** · decided by operator · recorded by Monster3-ddflow-7267fe

**Decision.** Operator 2026-10-31 (AskUserQuestion), own words: "Accept all recommended defaults (Recommended)". Task ACF-decide, decisions (1)-(5) as written there: no Accrete-specific adapter now, generic seams only; ddflow as SOURCE first (read-only why corpus export) and CLIENT second only through RQM-mcp-client; anything leaving the machine goes through an egress door with sensitivity ceiling, preview and confirm ([consult_out].confirm default true, never auto for agent-origin text) and a digest record; external answers are trust=imported, fenced, never an instruction or gate pass, unreachable is unavailable; D-graph-scope-r1 kept. Question (6) (which of ACF-recall-probe and ACF-memory-consolidate to build) had no recommended default and is asked separately.

Governs: `docs/ddflow/decisions/accrete-fit.md`

## Questions and the operator's answers

Answers, 2026-10-31, own words: "Accept all recommended defaults (Recommended)" for questions 1-5 (D-ACF-answers), and "Both" for question 6 (D-ACF-answers-6, which completes D-ACF-answers). Question 6 had no recommended default and was asked separately.

| # | Question | Answer |
|---|---|---|
| 1 | Accrete-specific adapter | none now; build generic seams only |
| 2 | Direction | ddflow as SOURCE first (read-only why corpus export); ddflow as CLIENT second, only through RQM-mcp-client |
| 3 | What may leave the machine | only through an egress door with a sensitivity ceiling, preview and confirm (`[consult_out].confirm`, default true, never auto for agent-origin text), recorded with a digest |
| 4 | External answers | trust=imported, fenced, never an instruction or a gate pass; an unreachable service is `unavailable` |
| 5 | Graph store | D-graph-scope-r1 kept: no knowledge-graph memory is built |
| 6 | ACF-recall-probe and ACF-memory-consolidate | "Both": build both (D-ACF-answers-6) |

Governs: `docs/ddflow/decisions/accrete-fit.md`.
