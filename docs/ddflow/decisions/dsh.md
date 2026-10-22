# dsh (DeepSeek Harness) support: tier, config, hooks, lanes, credentials

Decision document for task `DSH-decide`, phase `P-dsh-fit`. It restates decision `D-dsh-fit`; the log record is the source of truth (`ddflow decision show D-dsh-fit`). Background: `docs/ddflow/research/dsh.md`.

Operator 2026-10-22, answers (verbatim): 1. Tier: "Tier 1 live smoke now" (NOT the recommended fixtures-only): the operator consents to executing dsh for DSH-facts now. 2. Config: repo overlay file + printed launch line (`dsh --profile <p> --patch .ddflow/harness/dsh.cordis.yml`); never edit $DSH_HOME without per-agent consent. 3. Hooks: mount the experimental hooks-claude-code bridge from the overlay, fail open; lease enforcement via PreToolUse only when the bridge is verified loaded, else reported unavailable. 4. Lanes: lane-private DSH_HOME, workspace-write, approvals resolved by the ddflow permission broker over ACP; headless danger-full-access only as explicit operator opt-in in a disposable worktree/container. 5. Credentials env-only, never argv or committed; telemetry recorded, not toggled. 6. No dsh reviewer. 7. Defer the npm bundle. 8. dsh sessions must declare their model (confirmed).

## Decisions

| # | Question | Decision | Rejected |
|---|---|---|---|
| 1 | Tier | Tier 1: live smoke now, executing dsh for `DSH-facts` (consent per D-agent-compat, install only into a scratch directory, user's Node, no sudo) | Tier 2 fixtures-only until a stub-endpoint headless lane exists |
| 2 | Config location | A ddflow-owned overlay `.ddflow/harness/dsh.cordis.yml` plus a printed launch line `dsh --profile <p> --patch .ddflow/harness/dsh.cordis.yml`; `$DSH_HOME` is never edited without per-agent consent | Merging into the user's profile patch by default |
| 3 | Hooks | Mount the experimental `hooks-claude-code` bridge from the overlay, fail open; lease enforcement via `PreToolUse` only when the bridge is verified loaded, otherwise reported `unavailable`, never claimed | Native Cordis plugin; assuming the bridge is loaded |
| 4 | Lane permission | Lane-private `DSH_HOME`, `workspace-write`, approvals resolved by the ddflow permission broker over ACP; headless `danger-full-access` only as an explicit operator opt-in in a disposable worktree or container | Headless `danger-full-access` as the default |
| 5 | Credentials, telemetry | Credentials from the environment only, never argv, never committed; telemetry is recorded, not toggled, EXCEPT for ddflow-launched lanes (row 9, `D-dsh-telemetry`) | Writing keys into overlay or profile files |
| 6 | Reviewer | No dsh reviewer; direct OpenAI-compatible reviewers stay (a review is a diff in and a verdict out) | An agent-loop reviewer |
| 7 | npm bundle | Deferred; overlay first (`DSH-bundle` stays optional) | Publishing a dsh bundle package now |
| 8 | Model family | Independence is judged from the model declared at session start, so dsh sessions must declare their model; no harness-level model family exists. The descriptor field `family` is only a substring key matched against MCP `clientInfo.name` (`dsh` matches `dsh-mcp-client`); review independence never reads it | A harness-level model family |
| 9 | Telemetry for lanes (operator 2026-10-22, `D-dsh-telemetry`, amends row 5 for lane launches only) | The ddflow-written dsh overlay sets `DSH_TELEMETRY_MODE=DISABLED` for ddflow-launched lane workers; the user's own dsh usage is untouched; doctor reports the exporter (`dsh-otel-collector.deepseeksvc.com`, feedback-gated) and the overlay choice. `DSH-overlay` and `DSH-lane` carry it | Record only, never toggle; disabling for all dsh use |

## Consequences

- `DSH-facts` runs dsh for real (live smoke is authorised; fixtures-only was rejected), in a scratch directory outside the repo. The coordinator's brief adds a safety limit on the endpoint: a local stub or LAN endpoint, not a real provider key. That limit is a property of this first run, not of the tier; a keyed run later is allowed by decision 5 (env only).
- A forgotten `--patch` silently removes ddflow from a dsh run; `DSH-onboard-verify` must catch it with `--dump-config`.
- Unattended lanes need either an ACP answerer or a disposable tree; otherwise asks fail closed.
