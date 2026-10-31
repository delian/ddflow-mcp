# Specs: recommended defaults accepted (one spec kind, computed lifecycle, digest baselines, person approval, section injection, warn-by-default drift)

Decision document for the task `SPEC-decide`. It restates the decision record below; the record in the event log is the source of truth (`ddflow decision show D-SPEC-answers`).

## D-SPEC-answers: Specs: recommended defaults accepted (one spec kind, computed lifecycle, digest baselines, person approval, section injection, warn-by-default drift)

status **accepted** · decided by operator · recorded by Monster3-ddflow-7267fe

**Decision.** Operator 2026-10-31 (AskUserQuestion), own words: "Accept all recommended defaults (Recommended)". Task SPEC-decide, decisions (1)-(7) as written there: one kind spec with class feature|design|interface|acceptance; lifecycle draft, in_review, approved, implemented, verified, superseded, withdrawn with implemented/verified computed; versioning by record history and content digest with named baselines; a person approves feature/design/acceptance specs, an interface spec whose checks pass may be agent-approved, never the author's family as sole reviewer; claim-time injection of cited sections capped by [specs].claim_tokens; drift policy warn by default, block per spec or project as a knob; the specs gate only where an approved spec's scope matches.

Governs: `ddflow/templates/kinds/spec.toml`
