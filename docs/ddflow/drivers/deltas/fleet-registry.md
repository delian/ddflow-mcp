# Fleet registry (DX-fleet-registry)

A lane's name is its agent identity, so `<lane>` below is the agent's name.

`ddflow lanes fleet` lists every agent the log has seen (default: all history, knob
`[lanes] fleet_window_s`) with role, harness, model, the items it leased, the seconds left on
its lease, heartbeat and the last stop reason the log records (session summary, lease-expiry
reason, release note). A later normal release by the agent itself, lease or session clears an older reason. Role
stays empty until `session.started` records one. Classes: failed, stalled, idle, working,
done; `[lanes] fleet_failed_patterns` decides which reasons mean failed. Lost work comes first,
then live work; `[lanes] fleet_max_rows` cuts the listing and says how many more there are.

`ddflow lanes resume <lane>` answers how to resume a stopped lane: read-only and idempotent,
eligible only while its item is unfinished and not held by someone else. A finished item is
not resumable, and an item someone else holds names that holder. It offers `ddflow lanes brief <lane>`, which reaches lanes past the lane window
too. It restarts nothing: that is `ddflow resume <item>` (DX-resume-command) and a lane launch
from an approved template.

No new event kind and no second store: a fold over the lane ledger plus existing events.
