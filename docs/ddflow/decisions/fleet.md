# Fleet

Decision document for the task `FL-decide`. It restates the record below; the record in the event log is the source of truth (`ddflow decision show D-fleet`). Evidence: research note R-fleet.

## D-fleet

```
D-fleet — Fleet: build all of P-fleet; external orchestrators may claim within scoped tokens and caps, billable runs ask; review cap 5, threads 6, depth 1; scorecards auto-shift routing within the governor range
  status accepted · decided by operator

Context:
  Operator 2026-10-14 asked what Codex's multi-agent features, Symphony and Frontier mean for ddflow; note R-fleet.

Decision:
  Operator 2026-10-14 (AskUserQuestion, FL-decide). (1) SCOPE: all P-fleet tasks are built in the queued order (spike first, then backends, caps, fan-out, tracker contract, receipt, roster). (2) WHO STARTS: an external orchestrator (Symphony or another tracker-driven one) may claim and report through the tracker contract with a scoped token; ddflow-started Codex runs stay inside the lease and the group caps; any billable or cloud run asks the approvals engine first. (3) DEFAULTS, all knobs: review-capacity cap [schedule.review] max_waiting = 5; Codex agents.max_concurrent_threads_per_session follows the group cap (default 6); agents.max_depth = 1. (4) ROSTER: the operator chose AUTO-APPLY WITHIN THE GOVERNOR'S ALLOWED RANGE (not the recommended proposals-only): scorecards may shift routing by one tier inside the [govern] limits, every shift is a logged, reversible event with the scorecard evidence, and the guardrails the option text named stay fixed: reviewer rules (the cross-family requirement, D-reviewer-trust) are unchanged and a tier floor or security-relevant glob pin (GV-guardrails) is never moved by a scorecard.

Consequences:
  FL-scorecard applies routing shifts itself inside [govern] limits and logs them; FL-tracker-contract needs scoped tokens (CP-scoped-tokens); FL-review-cap ships max_waiting=5.

Alternatives rejected:
  Core only; spike only. Read-only orchestrators; operator only. Review cap 3 and threads 4; no cap. Scorecards as proposals only (recommended, not chosen); display only.

Governs: docs/ddflow/decisions/fleet.md, ddflow/services/lanes/**, ddflow/services/scorecard*.py
```
