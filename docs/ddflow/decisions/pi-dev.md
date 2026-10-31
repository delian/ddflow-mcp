# Pi support: harness first, Pi package auto-written on adopt, closed gate only on refuse, telemetry off in lane config, tier 2, version floor from probe

Decision document for the task `PI-decide`. It restates the decision record below; the record in the event log is the source of truth (`ddflow decision show D-PI-answers`).

## D-PI-answers: Pi support: harness first, Pi package auto-written on adopt, closed gate only on refuse, telemetry off in lane config, tier 2, version floor from probe

status **accepted** · decided by operator · recorded by Monster3-ddflow-7267fe

**Decision.** Operator 2026-10-31 (AskUserQuestion, PI-decide): (1) order harness, then lane worker, then session ingest (recommended). (2) adopt MAY write the executable TypeScript extension into .pi/extensions AUTOMATICALLY (the operator chose "Yes, automatically on adopt", not the recommended 'ship a Pi package'); it runs in Pi's process with the user's permissions and loads only after Pi's own project trust, so adopt must say exactly what it wrote. (3) gate hooks fail closed only when ddflow answered refuse; ddflow unreachable is unavailable, recorded, never a block (recommended). (4) telemetry: ddflow may set enableInstallTelemetry=false in lane config only (recommended). (5) tier 2: recorded fixtures, no live smoke; a live smoke needs a provider key and operator consent (recommended). (6) version floor: probe the installed version, floor at it, warn below, never block (recommended).

**Alternatives rejected.** ship only a Pi package the operator installs with pi install (recommended, not chosen)

Governs: `docs/ddflow/decisions/pi-dev.md`, `ddflow/harnesses/pi.toml`


## Questions and the operator's answers

Answers, 2026-10-31 (AskUserQuestion, PI-decide), quoted from D-PI-answers. Research: `docs/ddflow/research/pi-dev.md`. Question 2 departs from the recommendation; the rest are as recommended.

| # | Question | Answer |
|---|---|---|
| 1 | Direction order | harness, then lane worker, then session ingest (recommended) |
| 2 | Executable TypeScript extension in `.pi/extensions` | **changed**: "Yes, automatically on adopt"; adopt MAY write it automatically (the recommended 'ship a Pi package the operator installs with `pi install`' was not chosen). It runs in Pi's process with the user's permissions and loads only after Pi's own project trust, so adopt must say exactly what it wrote |
| 3 | Gate hooks closed or open | fail closed only when ddflow answered refuse; ddflow unreachable is `unavailable`, recorded, never a block (recommended) |
| 4 | Telemetry | ddflow may set `enableInstallTelemetry=false` in lane config only (recommended) |
| 5 | Tier in D-agent-compat | tier 2: recorded fixtures, no live smoke; a live smoke needs a provider key and operator consent (recommended) |
| 6 | Minimum Pi version | probe the installed version, floor at it, warn below, never block (recommended) |

Governs: `docs/ddflow/decisions/pi-dev.md`, `ddflow/harnesses/pi.toml`.
