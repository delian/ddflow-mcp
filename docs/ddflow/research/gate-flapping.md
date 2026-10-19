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
  (0 findings), 1257 passed otherwise (triage, budget, refutation). A third of the
  gate's passes are settlements, which is exactly the shape that reads as a flip.

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

Count a flip only when the opposite verdict comes from a NEW review run of the gate (a
`gate.started` between the two outcomes), not from the triage or settlement of the previous
round. Regression test: a failed review round followed by a triage pass, repeated five
times, is not `gate_flapping`; an alternating started/failed/started/passed run still is.
