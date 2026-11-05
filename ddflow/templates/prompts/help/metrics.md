# Metrics: where the time went, from the log

Every figure is derived from the event log alone (the fold, the critical path, the gate
records), so there is nothing to record and nothing to drift. It writes no event.

    ddflow metrics                   cycle time (claim to done), time per gate, review rounds per
                                     item, failure / skip / unavailable rates, queue and glob wait,
                                     rework (completed items a later bug hit)
    ddflow metrics --group-by G      the rates per gate (default), reviewer, agent, kind or phase
    ddflow metrics --phase P --since 7d   one phase; since a duration back or a date YYYY-MM-DD
    ddflow metrics forecast          the critical-path ETA of a phase as a P50..P90 range
    ddflow metrics retro <id>        one item's timeline and its biggest gap: why it was slow
    ddflow metrics skips             the gate-skip audit: rate per gate, reasons, skips no later
                                     pass made good, skips by agent
    ddflow metrics lanes             lane utilisation on an hourly grid (working vs idle beside ready work)
    ddflow metrics stages            the cycle-time split: queue wait, lease wait, implement, per-gate
                                     run and host-lock wait, review wait, merge wait, claim to merge,
                                     rework rounds, gate re-runs; `--group-by phase|kind|size|agent`
                                     (size = changed lines, bounds `[metrics] stage_size_small/large`).
                                     Claim refusals and human wait are listed as not measured: ddflow
                                     records no event for them yet (P-observe.telemetry), never 0.
    ddflow metrics locks             what the leases cost: hold split (claim to first gate, gates,
                                     reviews, last review to release, after merge), claim waits and
                                     false serialization, declared versus written files, merge-tree
                                     conflict and red-main rates; says when a falsifier
                                     (`[metrics] lock_falsifier_*_pct`) tripped and the call that
                                     switches back to strict locking (`lock_revert_key/value`).
    ddflow metrics swarm             the baseline a lane fleet must beat: merged items per hour,
                                     rework rate, agent-hours per item, dollars when events carry
                                     `cost_usd`, critical-path slack and parallelism; `--phase` and
                                     `--since` give the window. `ddflow metrics` carries the same
                                     headline rows as `swarm.*`.

A figure that rests on fewer than `[metrics] min_samples` samples is `n/a`, never 0, and a
view with no figure at all exits 2 ("not enough data"). The forecast is an ESTIMATE of the
floor: the longest unfinished dependency chain, each link drawn (bootstrap, seeded) from the
durations of completed items of its kind, with unlimited agents and no rework.

Other services add metrics without editing this one: `services.metrics.register(name, fn)`
(rows with `metric`, `group`, `n`, `value`) and `register_dimension(name, fn)` for a new
`--group-by`. Over MCP it is `ddflow_metrics` with `view` = summary, forecast, retro, skips,
lanes, stages, locks or swarm. The `[metrics]` knobs set the minimum sample, the forecast draws, seed and percentiles,
and the grid.
