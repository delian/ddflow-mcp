# Agent lanes (`Lane`)

How ddflow starts, watches, steers and stops a coding agent. Port and registry:
`ddflow/adapters/lanes.py`; the contract (`LaunchSpec`, `LaneEvent`, `LaneStatus`, `Result`,
the capability set) is the P-drivers Lane contract adopted as the port (ADP-agent-lanes), not a
second design. `ddflow/services/lane_driver.py` re-exports it and keeps `spec_from_headless`,
the descriptor glue. Details of the transports: [lane-driver](../lane-driver.md).

- **Default adapter**: `process-headless` (`ddflow/services/lane_headless.py`), an unattended
  child process spawned through `infra.proc` (the adapters layer imports no `subprocess`); it
  registers itself with `register_transport`. `OpenCodeLane` is a second transport on top of it.
  Further transports (ACP, tmux) register the same way and a name taken by another factory is
  refused.
- **Selecting**: `driver_for(name)` returns the lane or an `unavailable` Result naming the
  known transports; `advertise(lane, wanted)` limits a request to what the lane can do.
- **Fake**: `FakeLane(transcript, exit_code=, capabilities=, refuse=)` in
  `ddflow/adapters/fakes/lanes.py` replays `(event, fields)` pairs as canonical events, honours
  the event buffer, records steering when built with `steer`, and reports `unavailable` like a
  real transport otherwise. Swarm and ETA tests use it instead of an agent.
- **Conformance**: `tests/test_adapter_lanes.py` runs one case list against `ProcessHeadless`
  (a child replaying `tests/fixtures/lane_driver/ok.jsonl`) and the fake.
- **Rules**: argv is a list, never a shell line; secrets travel by env name; a capability a
  transport lacks is `unavailable`, never imitated.
- **Still open**: which harness a lane launches is descriptor data (`ddflow/harnesses/*.toml`);
  the agent-literal baseline entries for lane code (`lanehooks`, `launch_template`,
  `lanes/opencode`) shrink in a follow-up that moves them into descriptors.
