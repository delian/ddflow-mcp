# Lane driver contract

Code: `ddflow/services/lane_driver.py` (contract and registry), `lane_headless.py` (the
`process-headless` transport). Decision D-DRV-4; research `research/driver-architecture.md` 4.4.

## The contract (small and stable)

```
Lane.capabilities: frozenset[str]            # most the transport can do
Lane.start(spec: LaunchSpec) -> LaneHandle | Result     # Result = unavailable | failed
Lane.events(h, idle_s=None) -> Iterator[LaneEvent]      # canonical, bounded, redacted
Lane.steer(h, text) -> Result                # unavailable unless "steer" in h.capabilities
Lane.stop(h, grace_s=None) -> Result         # TERM, wait, group KILL, remove private files
Lane.status(h) -> LaneStatus
```

Capabilities: `steer interrupt resume usage tool_events permission_broker`. A handle carries its
own set (a lane with no stream rules claims no `usage`). A missing capability returns
`Result("unavailable", why)`; no driver imitates one. `Result.status` is `ok | unavailable | failed`.

`LaneEvent(seq, lane, event, fields)`; `event` is one of `started tool usage settled limit error`
(E5). `LaneStatus` states: `starting running exited failed stopped`, plus pid, exit code and its
meaning (descriptor `exit_codes`), a detected usage limit (E6) with its reset text, the agent's
session id, and counters `events skipped dropped`.

## Building a spec

`spec_from_headless(name, headless, autonomy=..., model=, effort=, max_turns=, mcp_config=,
session=, cwd=, env=, fixed_env=, home_env=, pass_env=, stream_rules=, limit=, scope=, ...)`
turns a descriptor's `[headless]` into a `LaunchSpec`: the argv template plus each named flag
with its whole-element placeholder (E4), the `[autonomy]` profile flags, and the output flag. A
descriptor gap (no template, no flag for a value asked, missing autonomy profile) is `unavailable`.

## Rules every transport keeps

- argv list, never a shell; executable never a placeholder (E4 refuses a shell).
- Keys by env NAME: `env = {child: SOURCE_NAME}`; values are read at launch, kept out of reprs,
  statuses and error text, and replaced by `[redacted]` in event fields. A key variable that is
  not set refuses the launch.
- Lane-private home: `HOME` (and `home_env`, e.g. `CODEX_HOME`, `DSH_HOME`) point at a 0700
  directory; only `[lanes].pass_env` names and the spec's own env reach the child. Telemetry-off
  literals (D-dsh-telemetry) go in `fixed_env`. A temporary home and any prompt file are removed by `stop` (call it after the lane ended too); a caller's
  is not.
- Cleanup is one process-group kill (`infra.proc.kill_group`), optionally inside a systemd scope
  (`[lanes].scope = systemd`; unavailable, never silently dropped, where systemd-run is missing).
- Bounded: stream lines over the engine limit are skipped; unread events drop oldest and count
  (`[lanes].event_buffer`).

Knobs: `[lanes].stop_grace_s`, `event_buffer`, `scope`, `pass_env`.

## Adding a transport

Implement the five calls plus `capabilities`, then `register_transport("acp", factory)` at import
and list the module in `BUILTIN_TRANSPORTS` if it ships in the tree. Planned: `acp`
(AC-backend), `rpc-jsonl` (Pi), `http-server` (OpenCode, Kimi web), `tmux`
(CP-backend-iface); `launcher` (P-agent-parity.launcher) calls `driver_for(transport)`.
`process-headless` has no `steer`: the prompt is its argv; a resumed session is a new `start`.
Test a driver with a fake agent replaying recorded stdout and exit codes
(`tests/fixtures/lane_driver/fake_agent.py`).
