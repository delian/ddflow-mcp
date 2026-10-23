# Decision: review round cap (D-review-rounds-4-r2)

Source: QD-decide-review-rounds, queue dedupe review 2026-10-23. Operator answer 2026-10-23, verbatim: "make default 2, for this project 4, with user acceptance (via agent chat, or cli) can be extended to more, unless for any of these there is a corporate policy saying other, or the user has changed the configuration".

## Decided
- Shipped default `[review].max_rounds = 2` (ddflow/config_sections/review.py stays).
- This project's `.ddflow/config.toml` sets 4.
- A round beyond the cap needs the user's acceptance: through the agent chat (the remote-approvals path, P-remote-approvals) or the CLI (`ddflow grant` / `review --force --reason`, recorded and reported).
- Never past a corporate policy limit: a company floor or ceiling (D-config-layer-order layer 9, strength per P-graded-policies) is tighten-only and wins over the default and over the user's acceptance.
- An explicit user configuration wins over the shipped default (it is the user's own knob).
- RV-round-cap only repeated the 2-round default and delta rounds already count in `max_rounds` (B-gate-econ-review-budget, done): abandoned.

## History kept
D-review-rounds-4 (2026-10-21, "lets say 4 or 5", 5 rejected as a config) is superseded by this record. The fifth round that leads authorised under "the operator's 4-5 range" is history: from now it is an accepted extension, not a range.

## Related
P-graded-policies (company limits are policies), P-remote-approvals (acceptance through chat), D-config-layer-order, DX-review-delta-superseded and DX-review-concurrent-runs (reworded: the budget is the knob, not "2").
