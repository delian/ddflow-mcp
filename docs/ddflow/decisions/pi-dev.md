
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
