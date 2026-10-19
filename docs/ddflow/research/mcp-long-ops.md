# MCP long operations: detach, poll, or push

Research item RM-long-ops-research, 2026-10-19. Marks: CONFIRMED = probed or read in code on this
machine; CLAIMED = read in vendor docs or issue titles, not probed.

## 1. Answer in five lines

1. The falsifier ("only emit progress") holds for ONE client, Claude Code, and only for the IDLE
   timer: CONFIRMED by probe. It fails for the wall-clock limit and is unproven or false for the others.
2. ddflow already emits progress for `ddflow_review`, `ddflow_merge`, `ddflow_upgrade`, `ddflow_pins`
   (`wants_progress`) but NOT for `ddflow_gate_run`, `ddflow_gate_verify`, `ddflow_ci`, `ddflow_verify`, `ddflow_bisect`
   (CONFIRMED in code). Claude Code's DOCUMENTED idle window for stdio is 30 minutes (CLAIMED; the probe only measured a 30 s floor
under a 15 s setting): that is very likely the
   "30 minute background limit" of DX-gate-background-limit / Bec9936dc6b.
3. Nothing a server pushes wakes an idle agent in any client except Claude Code's experimental
   `claude/channel` (research preview, interactive only). Polling is the only portable contract.
4. MCP tasks (2025-11-25 experimental, 2026-07-28 extension `io.modelcontextprotocol/tasks`) have the
   right shape (handle, status, poll interval, cancel) but no probed client sends the capability.
   Our handle should be field-compatible so it maps to tasks later.
5. The worker already outlives the call (B9abc247444); what is missing is a HANDLE returned early.

## 2. MCP specification

### Progress (2025-11-25, utilities/progress) CONFIRMED (fetched)
> "The receiver **MAY** then send progress notifications ... The `progress` value **MUST** increase
> with each notification" ; "Receivers of progress requests **MAY** choose not to send any progress
> notifications, send notifications at whatever frequency they deem appropriate".
Progress is opt-in by the REQUEST (`_meta.progressToken`). It is not a timeout mechanism.

### Timeouts (basic/lifecycle) CONFIRMED
> "Implementations **SHOULD** establish timeouts for all sent requests ... the sender **SHOULD** issue a
> cancellation notification ... Implementations **MAY** choose to reset the timeout clock when receiving a
> progress notification ... However, implementations **SHOULD** always enforce a maximum timeout,
> regardless of progress notifications."
So resetting on progress is a MAY and a hard maximum is a SHOULD: no client is obliged to honour progress.

### Cancellation (utilities/cancellation) CONFIRMED
> "Receivers of cancellation notifications **SHOULD**: Stop processing the cancelled request ... Not send a
> response"; "For task-augmented requests, the `tasks/cancel` request **MUST** be used instead."
ddflow deliberately ignores `notifications/cancelled` for offloaded tools (so the run survives).

### notifications/message and logging
2025-11-25: server `logging` capability; log messages are for the client application. 2026-07-28 changelog
(CONFIRMED): "Remove ... `logging/setLevel` ...; servers MUST NOT emit `notifications/message` for requests
that did not include" `io.modelcontextprotocol/logLevel`; Logging, Sampling and Roots are DEPRECATED
(SEP-2577). Request-scoped `notifications/progress` and `notifications/message` "continue to flow on the
response stream of the request they relate to". Nothing says a host must show them to the model.

### Resource subscriptions
2025-11-25: `resources/subscribe` + `notifications/resources/updated` (CONFIRMED); the spec says resources
are "application-driven", i.e. the host decides. 2026-07-28: replaced by `subscriptions/listen`, a single
long-lived POST response stream (HTTP-shaped; on stdio ddflow's `mcp_protocol.py` states nothing sends
server-initiated frames today). Probed clients: none surfaced one (see matrix).

### Sampling / elicitation
2026-07-28 replaces server-initiated requests by MRTR: the server returns `resultType: "input_required"`
and the CLIENT retries with `inputResponses` (CONFIRMED, changelog item 7). Sampling is deprecated.
Neither can start an agent turn: sampling asks the CLIENT to run an LLM completion under client approval;
elicitation asks the USER. They are not wake-ups.

### Tasks
2025-11-25 (experimental, CONFIRMED fetched): `tools/call` with `params.task.ttl` returns
`CreateTaskResult` {taskId, status working, ttl, pollInterval ms}; `tasks/get`, blocking `tasks/result`,
`tasks/cancel`, optional `notifications/tasks/status` ("Requestors MUST NOT rely on receiving this"),
tool-level `execution.taskSupport` required|optional|forbidden. Client-driven: the client must ask.
2026-07-28 extension `io.modelcontextprotocol/tasks` (CONFIRMED fetched): server-directed, "returns a task
handle unsolicited" only if the client declared the extension in per-request
`io.modelcontextprotocol/clientCapabilities`; `resultType: "task"`; `tasks/get` carries `result`/`error`
on terminal states; `pollIntervalMs`, `ttlMs`; `tasks/update` for input; `tasks/cancel` cooperative; no
`tasks/list`, no blocking `tasks/result`. "Never return a task to a client that did not declare support."
The MCP client-support matrix page lists NO client for tasks (it only tracks four other extensions).
Verdict: THEORETICAL for ddflow today; design the handle to be a task.

## 3. Client matrix

| Client | Request timeout | Progress resets it? | Max call duration | Server notifications to the model | Wakes idle agent |
|---|---|---|---|---|---|
| Claude Code 2.1.296 (stdio) | per-server `timeout` / `MCP_TOOL_TIMEOUT`, default about 28 h | IDLE timer: YES, CONFIRMED probe. Wall clock: NO, CONFIRMED probe | wall clock hard | `notifications/message` NOT shown, CONFIRMED probe A; `list_changed` refreshes tools (CLAIMED docs) | only `claude/channel` (CLAIMED docs, research preview, flag, interactive only, ignored with `-p`) |
| opencode / Kilo (7.2.20 installed; fork) | SDK request timeout; `timeout` setting is for tool LISTING, default 5 s (CLAIMED docs) | issue titles "fix(mcp): enable progress timeout resets" #32477, "surface MCP tool progress" #32480/#49929 imply NOT by default (CLAIMED titles) | issues #54220/#54221 ask for a limit and backgrounding | not probed | not probed |
| Codex CLI | `tool_timeout_sec`, default 60 (CLAIMED docs) | docs silent; not probed | per config | not probed | no evidence |
| Gemini CLI | `timeout` per server, default 600000 ms (CLAIMED docs) | docs silent; mcp-tool.ts passes only an abort signal in the file read (CLAIMED) | per config | not probed | no evidence |

Probe (Claude Code, `claude -p --model haiku --strict-mcp-config`, stdio server `/tmp/mcp-probe/server.py`,
tool `sleepy`, 40 s, stdlib JSON-RPC, emits `notifications/progress` every 5 s when the call carried a
`progressToken`, and `notifications/message` "SECRET-LOG-n"). Outputs copied verbatim:

* A. `CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT=15000`, progress on, log on, 40 s: result
  `slept 40.0s, token=2`; the model reported "No log lines containing SECRET-LOG appeared". Total 46 s.
  So progress kept a call alive through an idle window shorter than the call, and Claude Code sends a
  `progressToken` (token=2). `notifications/message` never reached the model.
* B. same idle env, progress OFF: `MCP server "probe" tool "sleepy" sent no response or progress for 30s;
  aborting. If this server is configured in your MCP settings, set a per-server "timeout" (ms) ...`
  (note: 30 s although 15 s was set: a floor of 30 s in this build, CONFIRMED observation, not explained).
* C. `MCP_TOOL_TIMEOUT=15000`, progress ON, 40 s: `MCP server "probe" tool "sleepy" timed out after 15s`.
  Progress does NOT extend the wall clock.

Not probed (honest): opencode, Kilo, Codex, Gemini CLI (no model credentials in this session, and
`~/.config/kilo` is off limits); Claude Code over HTTP; `claude/channel`; Claude Code interactive
auto-backgrounding. Claude Code docs: calls over two minutes in the MAIN conversation move to a
background task (v2.1.212+, `CLAUDE_CODE_MCP_AUTO_BACKGROUND_MS`) with the result as a task
notification; CLAIMED, and excluded for subagents, `-p`, and IDE servers. ddflow agents are mostly
subagents or headless, so it does not help the fleet.

## 4. ddflow today (CONFIRMED in code unless noted)

* `ddflow_review`, `ddflow_gate_run`, `ddflow_gate_verify`, `ddflow_ci`, `ddflow_verify`, `ddflow_bisect`,
  `ddflow_wait` run in a worker PROCESS (`OFFLOADED`, `start_new_session`), at most 4 per connection
  (B5f209cb092). The server keeps answering; a client that gives up does not kill the run, which records its
  own outcome (B9abc247444). But the CALLER still waits on that one response: the handle is implicit.
* Progress: `ddflow_review/merge/upgrade/pins` only. `gate_run`, `ci` send none, so a 35 min silent gate
  on Claude Code stdio hits the 30 min idle abort even though the worker finishes.
* `gate run` leaves `.ddflow/runs/<item>/<gate>.running.<pid>.json` (B58842554a7) and records
  `gate.started`; leases renew from the polling loop (DX-long-op-renew, done).
* `ddflow job run <item> <cmd>` is detached with a log; `job list/end` collect. There are 112 unended jobs:
  `job end` is manual, so jobs are not yet a lifecycle.
* `ddflow wait --max-s` waits for READY items or a lease holder, not for a run: it is not a run poller.
* DX-gate-background-limit is abandoned (folded into DX-gate-run-detach). Its "30 minute cap" is, from this
  research, the client idle window, not a ddflow cap: ddflow has no fixed 30 min constant (grep:
  none in `services/gates`); the lease is 1800 s which the renewer covers.

## 5. Contract (recommended)

Detach first, poll always, progress as a courtesy, tasks later as a mapping.

### Run key
`run_key = sha256(op | item | head_sha | gate/reviewer | normalized args)[:16]`, prefix `R`. The same
request returns the SAME run (join), never a second process: look up the key, `alive(pid)`, return it.
A new head gives a new key. Idempotency is scoped: an IN-FLIGHT run is joined, and a run that ended
`passed`/`completed` within `[runs].retention_s` returns its recorded result (retry after the client lost the
reply). A run that ended `cancelled`, `failed` or `unavailable` is NOT sticky: the same request starts a NEW
run (the old record stays in history, linked as `previous`), and `force=true` mints a new key even over a
success. (Found by the rubber_duck review: otherwise one crash or one cancel poisons (op,item,head).)

### Handle (returned within `[runs].sync_wait_s`, default 20, knob in file/CLI/MCP/prompt)
```
{ kind: "run", run: "R3f2a...", op: "review", item, head, state: "running",
  started_at, elapsed_s, eta_s|null, poll_after_s, wait_tool: "ddflow_run", log: ".ddflow/local/runs/R3f2a.log",
  pid, host, lease_renewed_until, next: "ddflow_run run=R3f2a... wait_s=20" }
```
`kind` is ours; `resultType` is NOT used, it is the 2026-07-28 spec discriminator (`complete`, `input_required`,
`task`) and is only ever set to `task` for a client that declared the extension. Field-compatible with MCP
tasks 2026-07-28: `run`=taskId, `state` working|input_required|completed|failed|
cancelled (+ `unavailable` mapped to failed with statusMessage), `poll_after_s`=pollIntervalMs/1000,
`ttl_s`=ttlMs/1000. If the call finishes inside `sync_wait_s` the ordinary result is returned directly,
so short runs cost one call.

### Poll
One tool, `ddflow_run` (CLI `ddflow run status|wait|cancel`): `run=<key>`, `wait_s` default 0, max
`[runs].long_poll_max_s` (default 20 until RM-client-probes measures opencode/Kilo/Gemini; the one number known
is Codex `tool_timeout_sec` 60 s per its docs, CLAIMED, and an unprobed client with a 30 s timeout would break 45). Bounded long-poll: it blocks up to `wait_s` or until the state changes, answers with the
same handle plus `state`, `progress_line` (last log line), `elapsed_s`, `eta_s` (median of the last N runs of
the same op/gate, else null), and `poll_after_s` that backs off 5 -> 10 -> 20 s (capped by `long_poll_max_s`). On terminal state it
carries the result object exactly as the synchronous tool would (gate outcome, findings, evidence path),
and a `recorded: true` flag saying the gate/review outcome is already in the log. Unknown key: exit 2 naming
`ddflow run list`. The agent never infers completion from silence.

### Lease and liveness
The worker, not the caller, renews the lease while the run is alive (reuse `leasekeep`, DX-long-op-renew),
so an agent that sleeps 10 minutes between polls does not lose its item. A run whose pid is dead without an
outcome is recorded `unavailable` with the reason by the next poll or sweeper (reuse
`running.dead_runs`); never `passed`.

### Cancel
`ddflow_run cancel=true` is the only cancel; `notifications/cancelled` stays ignored for offloaded tools.

### Push (optional layers, all off by default)
1. progress notifications on EVERY offloaded tool, one per log line or every `[runs].progress_s` (30 s), so
   Claude Code never idles out and opencode-like clients that reset on progress benefit;
2. `notifications/tasks/status`-shaped frames when the client declared tasks (2026-07-28 extension): same
   handle, returned as `resultType: "task"`; `ddflow_run` is the fallback;
3. Claude Code `claude/channel`: out of scope until it leaves research preview (interactive only).

### Compare with MCP tasks
| | ddflow run handle | MCP tasks (2026-07-28 ext) |
|---|---|---|
| Created | server decides, joins by key | server decides, new id per call |
| Poll | `ddflow_run` bounded long-poll | `tasks/get` poll with pollIntervalMs |
| Result | same call, `result` field | `tasks/get` `result` |
| Cancel | explicit, cooperative | `tasks/cancel`, cooperative |
| Idempotent retry | YES (key) | no (task id is receiver-generated) |
| Survives restart | YES (log + pid marker) | "durably created" required |
| Client support | all (a normal tool) | none probed |
Keeping the handle a normal tool is what makes it work with every client today.

## 6. Tasks (ordered; see the queue for ids)

Wave 1 (shared engine, no client dependency)
1. RM-runs-core: run key, run record (`.ddflow/runs/`, reuse `services/jobs.py` launch/alive/status and the
   gate started-markers; no second process manager), `ddflow run status|wait|cancel|list`, the handle schema.
   This is the engine of P-remote.runs; its HTTP route is later.
2. RM-progress-everywhere: emit progress from every OFFLOADED tool (gate_run, gate_verify, ci, verify, bisect, wait),
   periodic `[runs].progress_s` even with no output. Smallest fix; removes the Claude Code 30 min idle abort now.
3. RM-run-reaper: `job end` automatic on exit (collect exit code from the wrapper, B-style), and the 112
   unended jobs report; run retention.

Wave 2 (first consumers)
4. DX-gate-run-detach: `gate run --detach` and `[gate].sync_wait_s`: on top of RM-runs-core.
5. DX-review-detach: `review --detach`, `[review].sync_wait_s`.
6. B-roborev-async-standards: `standards --async` becomes a run of op `standards` whose worker is
   `roborev wait --job N`; `gate collect` is `ddflow run wait`.

Wave 3
7. RM-ci-merge-detach: `ci run` and `merge` (merge only its slow ci-on-merge step) return handles.
8. RM-run-tool-mcp: `ddflow_run` tool with CLI parity test, bounded long-poll max and backoff, ETA from history.
9. P-remote.runs: GET /v1/runs/<key>, events, MCP tasks mapping (`resultType: "task"` for clients declaring
   the extension). Needs P-remote.serve.

Wave 3b (runs BEFORE item 8 fixes the poll defaults: RM-run-tool-mcp needs RM-client-probes; RM-client-probes needs RM-runs-core)
10. RM-client-probes: repeat the sleepy probe with opencode/Kilo, Codex, Gemini CLI when credentials exist;
    record matrix rows; adjust `long_poll_max_s` default.
11. RM-run-retention-and-driver: implement driver and deltas say "start, then `ddflow_run wait_s=20`"; never
    background shell + hand-polled files.

## 7. Self-assessment (D-ddflow-self-assessment-r2)

Defects and gaps found, with their records (no duplicates: dupes checked at filing):
* gate_run/gate_verify/ci/verify/bisect/wait send no progress: task RM-progress-everywhere.
* 112 unended jobs, `job end` manual: task RM-run-reaper.
* no run poller, handle only implicit: tasks RM-runs-core, RM-run-tool-mcp.
* review of a `--no-worktree` item sees an empty diff; roborev kilo agent model not found: bug B9a8c95a9da.
* unprobed clients: task RM-client-probes.
