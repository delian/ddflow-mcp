# Engineering requirements: decisions

Decision document for task `RQM-decide` (phase `P-requirements`). Operator answers of 2026-10-21 (AskUserQuestion), recorded verbatim in the session log. The decision records are the source of truth (`ddflow decision show D-requirements-model`, `D-requirements-layers`, `D-requirements-injection`).

Evidence: [requirements-management.md](../research/requirements-management.md) (research `RQM-research`). Same shipped descriptor as `P-product-docs` (`PD-req-kind`); the P-goals strength mapping is reused (`D-goals-principles`).

## 1. One kind, a class, three strengths (D-requirements-model)

- ONE kind `requirement` (the shipped `ddflow/templates/kinds/requirement.toml`), field `class` = `product | constraint | standard | policy`.
- `strength`: MUST (block; override = waive only, never none: the task body's `waive|none` was narrowed to `waive only` by the operator, reusing the enforcement x override mapping of `D-goals-principles`), SHOULD (warn; deviate with a reason), MAY (advisory).
- Rejected: a second `standard` kind (a second engine, a second set of verbs, duplicate-check and search to keep in step; the class field separates them without that cost). Rejected: `priority` as the strength carrier (it orders, it does not oblige).

## 2. Layers and who may accept (D-requirements-layers)

- Precedence org > team > project > local > task. A lower layer may only TIGHTEN (raise strength, narrow scope, add a verify) unless a person records a scoped, reasoned, expiring waiver.
- An agent may PROPOSE a requirement and may accept only the low-risk classes a knob names (the `D-product-docs-r3` pattern); a person accepts the rest.
- Remote sources (codeguide-mcp, a corporate URL) arrive `trust=imported`, pinned by digest, and are never applied above SHOULD without a person's approval.
- Rejected: free override by lower layers (an org floor would mean nothing); a `[policy]` duplicate section (the org enforced floor is `B-org-policy`).

## 3. Injection tiers and budget (D-requirements-injection)

| Tier | What | When |
|---|---|---|
| always-on charter | one line per MUST, never trimmed; over `[requirements].charter_tokens` is a doctor warning, not a trim | session start, re-sent after compaction |
| contextual | by glob / context / gate | claim and pre-edit |
| on-demand | search | agent asks |
| periodic digest | changed or high-risk set | at the reinstruct cadence |

- Charter cap: `[requirements].charter_tokens`, default **500** (the operator chose the larger charter over the recommended 250). Like every number it is a knob (file, CLI, MCP, prompt) and overridable per project and org.
- Rejected: a 250-token charter (would drop MUSTs sooner on a project with many standards); trimming the charter when over budget (a silently missing MUST is worse than a long charter; over the cap is a doctor finding instead).

## Consequences

- RQM-model carries the 500 default; RQM-layers, RQM-doors and RQM-sources implement sections 2 and 3.
- `P-requirements` stays held behind the core/docs wave (`D-old-work-behind-core-docs`); no dependency changed.
