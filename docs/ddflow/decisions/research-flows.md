# Research workflows: verifier, committed plan, confidence ladder

Decision document for the tasks `RF-decide`. It restates the decision record below; the record in the event log is the source of truth (`ddflow decision show D-research-flows-r2`).

## D-research-flows-r2: Research workflows: a verifier is required (with a judgement-only exit), plans are committed first for shipped workflows, a confidence ladder on the claim grade

status **accepted** · decided by operator · recorded by hy-commit-events · supersedes D-research-flows

**Context.** Operator 2026-10-13 asked for research of any kind (architecture, math, hardware, DSP, science) to run as managed workflows, standalone or inside development phases; note R-research-flows (verifier-backed systems produced real results, judgement-only ones fabricated).

**Decision.** Operator 2026-10-13 (AskUserQuestion): (1) A research item must name its verifier before it starts; 'judgement only' is an explicit exit that caps its confidence at asserted. (2) The pre-registered plan (question, rival hypotheses incl. a null, predictions, metric, stop rule, budget) is committed and hashed before the first run: it BLOCKS in the shipped workflows and WARNS for an ad hoc research item; a later change is a logged deviation. (3) A confidence ladder asserted/supported/verified on the existing claim evidence grade (verified = a machine check passed, supported = an independent model family agrees); only verified is quoted as fact. (4) Ship order: everything in the first wave. Remaining defaults accepted as one set: the skeptic and replicator are another model family (D-reviewer-trust); runners and verifier commands run only when the operator approves them by digest and an unavailable one is recorded unavailable, never passed; chaos, production-touching and hardware tests need an explicit human grant; a flake is quarantined 14 days with an owner and shown in the brief; a test-only repository pins the system under test by sha plus digest with a last-green-pair pointer; standalone research gets a worktree only when it changes files; stop rules and budgets are per-template knobs (default K=5 runs without improvement, saturation, budget). Three answers matched the recommendation; the ship-order answer ('everything in the first wave') took the non-recommended option.

**Consequences.** P-research-flows tasks RF-template, RF-plan, RF-stop, RF-confidence implement this; no new grading scheme; the judgement-only exit keeps the rule usable for architecture questions.

**Alternatives rejected.** Verifier advisory or strict with no exit; plan commit blocking everywhere or warn everywhere; a ladder that is only advisory or none.

Governs: `docs/ddflow/decisions/research-flows.md`, `ddflow/config_sections/research*.py`
