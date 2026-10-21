# MCP soft upgrades: decisions

Decision document for task `SU-decision` (phase `P-soft-upgrade`). Operator answers of 2026-10-21 (AskUserQuestion), recorded verbatim in the session log. Decision records: `D-soft-upgrade-architecture`, `D-soft-upgrade-rule`, `D-soft-upgrade-capability`.

Evidence: [mcp-client-matrix.md](research/mcp-client-matrix.md) (parts 1 and 2) and the `R-soft-*` research notes.

## 1. Architecture and transport (D-soft-upgrade-architecture)

- A stable stdlib-only stdio shim owns the client connection; a restartable worker does the work (options A+D). Hard upgrades use exec-restart of the shim.
- Shim-to-worker transport: stdio pipes to a child process (portable, including Windows).
- Rejected: in-process reload (stale module state, partial imports, no isolation on a failed load); a unix socket (not portable to Windows, adds a path and permission surface); option B or C alone (cannot cover a changed protocol or an old client).

## 2. The five-rung soft/hard rule (D-soft-upgrade-rule)

An upgrade is SOFT when any of: the interface digest is unchanged; the client honours `list_changed`; facade mode (`ddflow_search` / `ddflow_call`) is on.

1. digest unchanged: soft, nothing to announce.
2. client honours `list_changed`: soft, announce.
3. additive change: soft for every client; new tools appear after restart for clients that ignore `list_changed`.
4. changed schema of an existing tool: soft only if the worker accepts the previous arguments for one version (schema grace).
5. HARD: an incompatible change to an existing tool or prompt for a client that does not honour `list_changed`, or the shim or protocol itself changed.

Reconnect is a fallback tier only: no client lets a server request one, exit-and-respawn drops the in-flight call, and most clients keep the stale list. Rejected: treating every client that ignores `list_changed` as hard (the facade makes it soft).

## 3. Learning client capability (D-soft-upgrade-capability)

Client table by `clientInfo.name`, plus the 2026-07-28 `subscriptions/listen` opt-in, plus observation: one re-fetch within 10 s of a notification means honouring; two silent notifications in different sessions means ignoring. No handshake probe (it would add a round trip and a false signal for clients that answer without acting).

## Consequences

- SU-worker-proto, SU-shim, SU-digest, SU-upgrade-integration, SU-observe-client-capability and SU-reconnect-fallback implement these.
- `P-soft-upgrade` stays right after `P-tool-diet`; no dependency changed.
