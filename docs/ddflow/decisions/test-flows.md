# Test workflows: Markdown cards, filter chain, mutation, read-only tests

Decision document for the tasks `TF-decide`. It restates the decision record below; the record in the event log is the source of truth (`ddflow decision show D-test-flows`).

## D-test-flows: Test workflows: Markdown cards in tests/cards, filter chain plus mutation on changed lines, read-only tests as a warning, everything in the first wave

status **accepted** · decided by operator · recorded by hy-commit-events

**Context.** Operator 2026-10-13 asked for workflows for tests of every kind, written as Markdown unless the user says otherwise, inside big jobs or in test-only repositories; note R-test-workflows.

**Decision.** Operator 2026-10-13 (AskUserQuestion): (1) Markdown test cards live in tests/cards/ (configurable; other formats are document types) and are promoted to code after 3 unchanged runs or when on a per-commit path; exploration, human bench steps and visual judgement stay cards. (2) Generated tests pass a filter chain (builds, passes 5 times, raises coverage) and a mutation score on changed lines against a per-kind target; survivors become tasks; a bug test needs fail-before/pass-after. (3) The implementer being read-only on tests it did not write, and the review of a changed assertion, snapshot, tolerance or deleted test, is a WARNING by default, not a block (this differs from the recommendation, which was to block; blocking stays available as a setting). (4) Ship order: everything in the first wave. Remaining defaults accepted as one set: the skeptic and replicator are another model family (D-reviewer-trust); runners and verifier commands run only when the operator approves them by digest and an unavailable one is recorded unavailable, never passed; chaos, production-touching and hardware tests need an explicit human grant; a flake is quarantined 14 days with an owner and shown in the brief; a test-only repository pins the system under test by sha plus digest with a last-green-pair pointer; standalone research gets a worktree only when it changes files; stop rules and budgets are per-template knobs (default K=5 runs without improvement, saturation, budget). Same verifier and plan rules as D-research-flows (a verifier named, judgement-only exit).

**Consequences.** P-test-flows tasks TF-card, TF-promote, TF-filters, TF-mutation, TF-readonly implement this; TF-readonly ships warn-by-default with a block mode and the impossible-task exit.

**Alternatives rejected.** docs/tests location with manual promotion; coverage-and-repeats only or mutation-only quality gates; research first, test workflows later; blocking read-only tests.

Governs: `docs/ddflow/decisions/test-flows.md`, `ddflow/config_sections/testflows*.py`
