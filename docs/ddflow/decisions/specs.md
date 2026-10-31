# Specs: recommended defaults accepted (one spec kind, computed lifecycle, digest baselines, person approval, section injection, warn-by-default drift)

Decision document for the task `SPEC-decide`. It restates the decision record below; the record in the event log is the source of truth (`ddflow decision show D-SPEC-answers`).

## D-SPEC-answers: Specs: recommended defaults accepted (one spec kind, computed lifecycle, digest baselines, person approval, section injection, warn-by-default drift)

status **accepted** · decided by operator · recorded by Monster3-ddflow-7267fe

**Decision.** Operator 2026-10-31 (AskUserQuestion), own words: "Accept all recommended defaults (Recommended)". Task SPEC-decide, decisions (1)-(7) as written there: one kind spec with class feature|design|interface|acceptance; lifecycle draft, in_review, approved, implemented, verified, superseded, withdrawn with implemented/verified computed; versioning by record history and content digest with named baselines; a person approves feature/design/acceptance specs, an interface spec whose checks pass may be agent-approved, never the author's family as sole reviewer; claim-time injection of cited sections capped by [specs].claim_tokens; drift policy warn by default, block per spec or project as a knob; the specs gate only where an approved spec's scope matches.

Governs: `ddflow/templates/kinds/spec.toml`

## Questions and the operator's answers

Answer, 2026-10-31, own words: "Accept all recommended defaults (Recommended)". The task's recommended defaults (research `docs/ddflow/research/project-specifications.md` sections 6-8) were accepted as written.

| # | Question | Answer (as recorded in D-SPEC-answers) |
|---|---|---|
| 1 | One spec kind | one kind `spec` with `class` = feature, design, interface or acceptance; prose stays in a file, the record holds metadata, lifecycle and digest; ADRs stay decisions |
| 2 | Lifecycle | draft, in_review, approved, implemented, verified, superseded, withdrawn; implemented and verified are computed from linked tasks and checks, never set by hand |
| 3 | Versioning and baseline | record history plus content digest at every transition; named baselines; an approved spec is changed by a new revision that is reviewed again, or superseded |
| 4 | Who approves | a person approves feature, design and acceptance specs; an interface spec whose checks pass may be agent-approved; the author's model family is never the only reviewer; every approval is an event with approver and checks |
| 5 | Injection budget | no always-on charter; at claim, the cited sections capped by `[specs].claim_tokens` (task default 600); `spec show --section` on demand; imported spec text fenced, trust=imported |
| 6 | Drift policy | warn by default; block per spec or per project as a knob |
| 7 | `specs` gate | added only where an approved spec's scope matches the item |

Knobs named so far: `[specs].claim_tokens` (default 600, from the task) and the drift policy (warn or block, per spec or project). The task also asks to list every `[specs]` knob the later tasks read; the decision record names no others, and SPEC-surfaces declares the section.

Governs: `ddflow/templates/kinds/spec.toml`.
