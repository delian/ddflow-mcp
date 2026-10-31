# User stories: recommended defaults accepted (story kind, criterion kind, MoSCoW, derived delivery status, DoR/DoD, person accepts, file-based trackers)

Decision document for the task `US-decide`. It restates the decision record below; the record in the event log is the source of truth (`ddflow decision show D-US-answers`).

## D-US-answers: User stories: recommended defaults accepted (story kind, criterion kind, MoSCoW, derived delivery status, DoR/DoD, person accepts, file-based trackers)

status **accepted** · decided by operator · recorded by Monster3-ddflow-7267fe

**Decision.** Operator 2026-10-31 (AskUserQuestion), own words: "Accept all recommended defaults (Recommended)". Task US-decide, decisions (1)-(9) as written there with the research-section-7.1 amendment: a new kind story (level epic|story), personas are the stakeholder kind, acceptance criteria are their own small kind criterion, stored status proposed|draft|ready|accepted|deferred|dropped with delivery status derived, MoSCoW plus operator rank, DoR/DoD as listed, a person accepts (agents only for requirements.agent_accept_classes), trackers import first and file-based, story pack injection capped by [stories].charter_tokens. (10) A third level (theme/initiative) is NOT a level: it stays a label, the recommended default.

Governs: `ddflow/templates/kinds/story.toml`, `ddflow/core/**`

## Questions and the operator's answers

Answer, 2026-10-31, own words: "Accept all recommended defaults (Recommended)". The task's recommended defaults (research `docs/ddflow/research/user-stories.md` section 7, with the 7.1 amendment) were accepted as written; D-US-answers records them as follows.

| # | Question | Answer (as recorded in D-US-answers) |
|---|---|---|
| 1 | Story kind and hierarchy | a new kind `story` with `level` = epic or story |
| 2 | Personas | the shipped `stakeholder` kind, no new kind |
| 3 | Acceptance criteria (as amended in 7.1) | their own small kind `criterion` |
| 4 | Statuses | stored `proposed`, `draft`, `ready`, `accepted`, `deferred`, `dropped`; delivery status derived |
| 5 | Prioritisation | MoSCoW plus an operator-set rank |
| 6 | Definition of Ready and Done | "DoR/DoD as listed" in the task |
| 7 | Who accepts | a person; agents only for `requirements.agent_accept_classes` |
| 8 | Trackers | import first and file-based |
| 9 | Injection budget | story pack capped by `[stories].charter_tokens` |
| 10 | A third level (theme/initiative) beyond a label | NOT a level: it stays a label, the recommended default |

Governs: `ddflow/templates/kinds/story.toml`, `ddflow/core/**`.
