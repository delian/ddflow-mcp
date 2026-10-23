# Decision: one resolver, per-kind combiner (D-requirements-layers-r2)

Source: QD-decide-layer-orders, queue dedupe review 2026-10-23. The operator asked, verbatim: "is there a benefit to have a separate resolver?". The answer below is the LEAD'S REASONED ANSWER, not an operator decision: accepted by default so the operator can overrule it.

State: ACCEPTED BY DEFAULT, UNCONFIRMED by the operator (the lead's reasoned answer; the operator may overrule).

## Lead's answer, accepted by default ( operator asked for the benefit of a separate resolver; none found)
- No benefit in a separate resolver. ONE resolver follows D-config-layer-order (packaged < company defaults < user < project < machine-local < env < CLI < session < company enforced).
- What differs per kind is the COMBINER, not the resolver: override-by-nearest (later wins) for config values, tighten-only for policies, floors and requirements, union for lists (`key+` / `key-`), merge for tables.
- The requirement chain org > team > project > local > task is a VIEW of that order: org = layers 2 and 9, team = a conf.d directory inside the project layer 4 (or the org bundle's team subset), project = 4, local = 5, task = the session overlay (8) scoped to one item. "Tighten-only" is the layer-9 floor rule applied to strength (P-graded-policies).
- Requirement chains are not a second resolver. RQM-layers and GP-org cite D-config-layer-order; D-CM-Q2 already did.

## Why no separate resolver
Two resolvers would need the same inputs (the same files, layers and org bundle), two precedence tables to keep consistent and two sets of tests, and would disagree at the first conflict (written as authority org-first versus later-wins). A combiner per kind keeps one precedence and gives each kind its own merge rule.
