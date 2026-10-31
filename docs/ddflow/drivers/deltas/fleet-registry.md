# Fleet registry (DX-fleet-registry)

`ddflow lanes fleet` lists every agent the log has seen (default: all history, knob
`[lanes] fleet_window_s`) with harness, model, item, heartbeat and the last stop reason the
log records (session summary, lease-expiry reason, release note). Classes: failed, stalled,
idle, working, done; `[lanes] fleet_failed_patterns` decides which reasons mean failed.

`ddflow lanes resume <lane>` (a lane is an agent identity) is the one resume: pure and idempotent, eligible only while the
agent's item is unfinished and not held by someone else; it names the new holder otherwise,
and offers `ddflow lanes brief <agent>`. It spawns nothing.

No new event kind and no second store: a fold over the lane ledger plus existing events.
