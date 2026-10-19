# Why critic and rubber_duck gates "flap"

Question (operator, 2026-10-12): `ddflow loops` reports `gate_flapping` for critic and
rubber_duck (11 verdict changes on fix-Ba24a8d32ee, 8 on fix-Bfc9daca269). Reviewer
nondeterminism, round-budget semantics, chunked re-review repeating fixed findings, or a bug?

## Claim

The verdict changes are not instability. A review round that finds something records
`failed`; the author fixes it and triages the findings; the triage settlement (or the next
clean delta round) records `passed`; the next round fails again on a new finding. Each
round contributes two changes, so six rounds read as eleven flips. The detector
(`core/progress._gate_flapping`) counts adjacent `passed`/`failed` outcomes of a gate and
cannot tell a review loop converging round after round from a coin flip.

## Evidence

* fix-Ba24a8d32ee, rubber_duck: failed 17:01, passed 17:09 (9 min later, no review
  started in between), failed 17:21, passed 17:26, failed 17:36, ... passed 19:06. Every
  `failed` follows a `gate.started`; every `passed` follows a failure within 1-9 min and
  carries the failed round's `chunk_findings` or a `budget_forced` note: the settlement
  of that round, not a second reviewer opinion.
* fix-Bfc9daca269, critic: the same alternation; the `passed` rows with no
  `evidence.status` are triage settlements, those with `status: REVIEWED` and 0 findings
  are clean delta rounds.
* All 4475 recorded critic/rubber_duck outcomes: 2538 failed, 680 passed on a clean review
  (0 findings), 1257 passed otherwise (triage, budget, refutation). Of the 1937 passes,
  1257 (about two thirds) are settlements rather than clean reviews, which is exactly the
  shape that reads as a flip.

## Verdict: REFUTED as reviewer nondeterminism; CONFIRMED as a detector defect

* Reviewer nondeterminism: not needed to explain the data. Each failure names new,
  distinct findings (different chunk/location), not the same finding appearing and
  vanishing.
* Round-budget semantics: working as designed (D-gate-economy); a failed round stays
  failed until its findings are triaged.
* Chunked re-review repeating fixed findings: not seen; `--delta` and `--chunk` carry the
  prior findings forward.
* ddflow bug: `gate_flapping` counts a failed round and its own settlement as a flip.
  The advice it prints ("re-running it will not converge") is wrong for these items,
  which did converge.

## Fix (filed as a task)

Group a gate's outcomes into rounds (each `gate.started` opens one) and give a round the
LAST decisive verdict recorded before the next one starts: a failed round that its own
triage settled is `passed`. Count a flip only when consecutive rounds end differently.
Judged this way fix-Ba24a8d32ee, whose six rounds each ended `passed`, has no flips; a gate
whose round 1 ended `failed` (left unsettled) and whose round 2 ended `passed` still has one.
Regression test: five failed-then-settled rounds are not `gate_flapping`; an unsettled
failed round followed by a passing one, five times over, still is.
