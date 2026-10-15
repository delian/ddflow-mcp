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

A figure that rests on fewer than `[metrics] min_samples` samples is `n/a`, never 0, and a
view with no figure at all exits 2 ("not enough data"). The forecast is an ESTIMATE of the
floor: the longest unfinished dependency chain, each link drawn (bootstrap, seeded) from the
durations of completed items of its kind, with unlimited agents and no rework.

Other services add metrics without editing this one: `services.metrics.register(name, fn)`
(rows with `metric`, `group`, `n`, `value`) and `register_dimension(name, fn)` for a new
`--group-by`. Over MCP it is `ddflow_metrics` with `view` = summary, forecast, retro, skips or
lanes. The `[metrics]` knobs set the minimum sample, the forecast draws, seed and percentiles,
and the grid.
