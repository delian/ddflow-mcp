# Lanes: who is working, and what to do next

A **lane** is an agent identity working this project. The ledger is read from the event log
alone, so there is nothing to register and nothing that can drift; it never starts, stops or
resumes an agent and runs no daemon. Acting on a lane (starting or resuming it) is a different command, still to come.

    ddflow lanes                 each lane: harness, model, item, last heartbeat, state, how long
    ddflow lanes --watch         the same, redrawn when the log changes (or every watch_interval_s)
    ddflow lanes brief <lane>    that lane's whole first prompt: objective, assignment, lease and
                                 worktree, previous run (every agent-written text fenced as data)
    ddflow lanes advise          concrete moves, most urgent first, with the line to run
    ddflow lanes fleet           every agent the log has seen, past the lane window: role, harness,
                                 model, items, lease, stop reason; lost and live work first
    ddflow lanes resume <lane>   how to resume a stopped lane, or who took its item over; read-only
    ddflow lanes --coordinator   the coordinator's screen: running suites, lease holders (idle ones
                                 flagged after lanes.idle_s), pending approvals with their commands,
                                 ready work per phase and, with `--item ID`, that item's blocked-by
                                 chain; each section is cut at lanes.coordinator_max_rows
    ddflow lease                 the lease holders table alone (the same holders)

States:

- `working` — it holds a lease and has beaten inside `lease.ttl_s` (`lanes.stale_after_s`).
- `stale` — it holds the lease but has not beaten for that long; the lease has not lapsed yet.
- `waiting-on-glob` / `waiting-on-dep` — idle, and what is open is held by another lane's
  globs (the holder is named) or by an unfinished dependency.
- `idle-with-ready-work` — holds nothing while `ddflow next` has something to offer.
- `stopped (done)` / `stopped (session ended, item unfinished)` — its session ended.
- `stopped (reason unknown)` — the lease lapsed and no `session.ended` says why. Never
  guessed as context exhaustion or a crash: the log does not record those.

`advise` lists: a stopped lane with unfinished work (resume it with its brief), an item
blocked by a named lane's globs, lanes idle beside ready work, and more working lanes than
the parallelism limit. Over MCP it is `ddflow_lanes` with `view` = `state`, `brief` or
`advise`. Exit 2 means nothing to show. The `[lanes]` knobs tune the window, the stale
threshold, the watch interval and the size of advice and briefs.

## Role packs

`ddflow brief --role phase-closer|fixer|researcher|reviewer [--phase ID | --item ID]` prints the
project's standing rules for that role (tests first, ratchet offenders by name, the detached
suite recipe with the maximum timeout, the approvals relay protocol, one pins recipe per
approval, the docs-wave stop, no push), so a coordinator sends one line instead of retyping
them. The packs are `[roles]` config (`phase_closer_rules` and the like, one line each; a line
`rule:<id>` is rendered from that rule record), overridable in any config layer and cut at
`[roles].budget_tokens`. An unknown role refuses and lists the roles. `brief --for-agent NAME
ITEM --role R` appends the pack to the launch brief; over MCP it is `ddflow_brief` with `role`.
