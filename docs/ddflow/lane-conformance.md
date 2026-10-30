# Lane conformance

Code: `ddflow/services/swarm/conformance.py` (grading and the live run), `conformance_io.py`
(the stored level). Decision D-swarm-answers; research `research/swarm-orchestration.md` 7.3.

A lane is trusted with real items only at the level it passed. The run uses a stub agent on
stub endpoints and never a real key. Levels, each counting only when all below it passed:

| level | name | passes when |
|---|---|---|
| 1 | launch | the lane starts, emits `started`, stops cleanly |
| 2 | claim | a claim tool call that did not fail |
| 3 | heartbeat | a heartbeat tool call that did not fail |
| 4 | gate_record | a gate call (action record) that did not fail |
| 5 | hook | the caller's hook probe says the pre-tool hook fired (SW-lane-hooks) |
| 6 | lease_stop | the lane stops within `conformance_lease_wait_s` of its lease being revoked |
| 7 | tool_accuracy | at least `conformance_min_accuracy` of its calls are lean-core calls without error |

A check that could not run (no `tool_events` capability, no probe, fewer than
`conformance_min_calls` calls, lane gone before the revocation) is `unavailable`: the climb
ends and it is never recorded as passed.

```
rep = conformance.run(name, driver, spec, Params.from_config(cfg), hook_probe=..., revoke=...)
conformance_io.save(root, rep)                      # .ddflow/local/lane-levels.json
conformance.admits(conformance_io.load(root, name), needed, params)   # what SW-launch asks
```

Knobs `[swarm]`: `conformance_min_accuracy`, `conformance_min_calls`, `conformance_idle_s`,
`conformance_lease_wait_s`, `conformance_tools`, `conformance_max_age_s` (a level expires).
