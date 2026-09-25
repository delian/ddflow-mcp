# ddflow

A work queue for AI coding agents, and a memory of why the project is the way it is.

**The event log is the source of truth.** Every command appends to an append-only JSONL
log; the board, the queue, the search index and every report are projections of it. That
is what makes the whole thing recoverable: delete everything but `.ddflow/log/` and
`ddflow replay` rebuilds the project's decisions from nothing else.

Nothing here is a suggestion engine. `ddflow next` refuses to offer work whose
dependencies are unmet, `ddflow claim` refuses an item another agent holds or whose
files overlap one in flight, and `ddflow complete` refuses an item whose gates have not
been satisfied. The refusals are the product.

## The loop

    ddflow brief              what happened here, and what matters now
    ddflow next               what may start — already filtered by deps and conflicts
    ddflow claim <id>         lease it, and get an isolated worktree to work in
    ddflow gate status <id>   which gate is next, and what it wants from you
      ... do the work ...
    ddflow gate run <id> <g>  run a command gate; its exit code IS the evidence
    ddflow gate record <id> <g> --outcome ...   record an agent gate you performed
    ddflow complete <id>      refuses, listing what is unsatisfied, until it is
    ddflow merge <id>         land the branch from the primary checkout

Along the way, and not at the end: `ddflow lesson add` when something surprised you,
`ddflow decision add` when you chose between real alternatives, `ddflow bug found`
when you find one — a bug cannot be closed without a regression test.

## Exit codes mean the same thing everywhere

    0  it worked            2  nothing to do, or could not tell — an answer, not a failure
    1  it failed            3  refused: another agent, an unmet dependency, an open gate

`2` is never "no problem". A reviewer that could not run, a search with no hits and a
queue with nothing ready all report themselves rather than passing quietly.

## Topics

    ddflow help <topic>

{{ topics }}

## Everything it can do ({{ tool_count }} tools, the same on the CLI and over MCP)

{{ inventory }}

Every one of these is a CLI subcommand and an MCP tool; a test fails if the two surfaces
diverge. Add `--json` to any command for the machine-readable form.
