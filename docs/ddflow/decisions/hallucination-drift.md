# Hallucination and context-drift detection and recovery (proposal)

Status: **proposed**. Not a recorded decision until the operator answers HAL-decide. Research: `docs/ddflow/research/hallucination-drift.md`. Operator requests (2026-10-26, recorded verbatim as session prompts): detect model hallucination and language leaks (seen with DeepSeek 3.x Pro after about 350K tokens), and on detection log the context, compress it into a summary, close the lane and continue in a fresh one from a smaller context; follow-up: learn the healthy threshold per model automatically.

## Proposal D-drift

1. **Detect with deterministic, model-free signals first**: Unicode script and mixed-script analysis, invisible/bidi/replacement/mojibake characters, reference existence checks (paths, ddflow ids, CLI flags, symbols, packages), evidence-vs-gate-state checks, rule-violation counts, and per-model behavioural counters. Detector output is data, never an instruction; findings are records with evidence, never silent.
2. **Context canaries** at checkpoints: a fixed probe (item, globs, governing decisions, last commits) scored against ddflow's ground truth. Model-agnostic, no logprobs.
3. **A policy ladder** note, warn, checkpoint, quarantine, rotate; every step an event; suspect commits cannot merge or complete until re-reviewed or reverted; onset located with `ddflow bisect`.
4. **Rotation, not repair**: ddflow closes the lane, builds a seed from the event log, git and gate state (the agent's note is validated, unverifiable claims dropped), starts a fresh lane with seed + brief + governing decisions + read access to the recording, and resumes from the last green commit. For the lead, ddflow cannot close the session: it produces the same pack and advises (compact with ddflow-written instructions, or a new session re-grounded by `brief`/handoff).
5. **Per-model effective context** is a catalog fact `drift_onset_tokens` (source documented|learned|operator|default, confidence, samples, expiry), distinct from the advertised window. It can only pull the D-CC-Q4 / TE-context-caps marks lower; company floors and ceilings tighten-only.
6. **The threshold is learned**: change-point estimate over 50K fill buckets from the detector, reference, canary and gate signals, operating point = conservative bound minus margin; automatic changes only lower it; raising needs evidence, project opt-in and bounded steps; exploration via sandbox canaries first and a small share of low-risk lanes later; facts are `model.fact` events so replay rebuilds them; shared across projects as aggregates only.

Alternatives rejected: prompt-only instructions (the drifted agent is the reader); a language-ID model as the first detector (unreliable on short text, a dependency, unnecessary when script analysis answers); trusting the agent's own summary when rotating (compresses hallucinations in); a single global threshold (models differ: Claude shows no drift signal to the 950K bucket on this host, the operator reports DeepSeek at about 350K); automatic raising of thresholds without opt-in.

## Open questions for the lead (defaults in bold)

| Id | Question | Default |
|---|---|---|
| Q1 | Priority and placement? | **HIGH; wave 1 now (HAL-baseline-scan, HAL-detect-script, HAL-model-facts), the rest behind them** |
| Q2 | Scripts allowed by default? | **Latin only; a project adds scripts in config (`[drift].allow_scripts`); path allowlist for tests/fixtures; math symbols allowed** |
| Q3 | Enforcement of script hits? | **commit-msg: refuse on invisible/bidi/replacement or a foreign letter; pre-commit and hygiene: refuse on bidi/zero-width/replacement, warn (finding) on foreign letters in added lines outside the allowlist; accepted records silence one pattern** |
| Q4 | Canary probes on by default? | **On for unknown-family and models with a recorded onset, once per ladder mark (suggest/rotate) and after compaction; off elsewhere; unavailable is recorded, never passed** |
| Q5 | Seed rotate mark for DeepSeek family lanes until measured? | **300K (below the reported 350K); Claude and unknown per D-CC-Q4** |
| Q6 | Learned thresholds: auto-lower and auto-raise? | **Auto-lower on; raise off (report only); when raise is enabled: +25K per epoch, +100K above the shipped fact at most** |
| Q7 | Exploration beyond the threshold? | **Off in release 1; sandbox canary lanes first (monthly token cap), then 10% of low-risk items per SW-Q10 only with project opt-in** |
| Q8 | Lead handling? | **Advice and ddflow-written compact instructions only; ddflow never clears or restarts the lead; fresh-session pack from DX-handoff-pack** |
| Q9 | Share learned facts across projects/hosts? | **Off by default; when on, aggregates only (value, confidence, samples, window), at least 3 lanes per exported bucket** |
| Q10 | Language-ID model (lingua, fastText) as optional second stage? | **No in release 1; reconsider if same-script leaks (German in Latin script) appear in the ledger** |
| Q11 | Bug or finding kind for detector hits? | **Finding with evidence for review-style hits; bug (with regression test rule) only for a defect that reached committed code** |

## Queue

Phase `P-drift`: DX-drift-research (this), HAL-decide, HAL-baseline-scan, HAL-detect-script, HAL-model-facts, HAL-detect-refs, HAL-signals, HAL-canary, HAL-fact-records, HAL-ladder, HAL-hook, HAL-gate, HAL-digest, HAL-rotate, HAL-learn-onset, HAL-measure, HAL-lead-recover, HAL-explore, HAL-e2e, HAL-docs. Waves: 1 decide, baseline-scan, detect-script, model-facts; 2 detect-refs, signals, canary, fact-records; 3 ladder, hook, gate, digest, rotate, learn-onset, measure; 4 lead-recover, explore, e2e, docs. Reuses CC-resume-pack, DX-handoff-pack, SW-seed-pack, B-am-ingest, SW-ledger, DRV-model-catalog (HAL-model-facts is standalone until it lands). Related defect filed: Bb54029aee9 (agent-authored events carry no served model or fill).

Governs: `docs/ddflow/decisions/hallucination-drift.md`, `ddflow/services/drift_*.py`, `ddflow/services/model_drift_facts.py`
