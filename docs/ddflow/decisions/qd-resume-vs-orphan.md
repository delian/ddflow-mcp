# Decision: one recovery design (D-recovery-one-design)

Source: QD-decide-resume-vs-orphan, queue dedupe review 2026-10-23. Operator answer 2026-10-23 (relayed by the lead, not a verbatim quote): D-orphan-recovery governs; fold the four overlapping pairs of P-resume / P-orphan-recovery tasks.

## Decided
- D-orphan-recovery (operator 2026-10-18) is the one recovery design: SAFE_AUTO actions only, launcher resume off by default, kill switch `[recover].enabled`, recovery_attempts cap 2, rescue ref for dirty trees.
- One knob family `[recover]` (`enabled` kill switch, `auto = propose|safe`); the `[recover].auto = ask|safe|always|off` of B-res-resume is dropped.
- Folded (task fold, bodies and globs carried, dependants re-pointed): B-res-resume into DX-resume-command; B-res-brief-recovery-link into DX-recover-brief-slice; B-res-heartbeat into DX-health-watch-liveness; B-res-inflight-gates into DX-health-orphan-wait.
- Kept, as distinct deliverables: B-res-chaos-tests, B-res-driver, B-res-session-lifecycle, B-res-merge-journal, B-res-merge-landed (now a need of DX-resume-command). P-resume closes when they are done.

## Needs dropped by the folds
B-res-resume had needs B-res-merge-landed (re-added to DX-resume-command); B-res-brief-recovery-link and B-res-heartbeat needed B-hx-hook-core (done); B-res-inflight-gates needed B-roborev-async-standards (not re-added as a hard edge: long chain; noted on DX-health-orphan-wait). Gap: DX-fold-carries-needs.
