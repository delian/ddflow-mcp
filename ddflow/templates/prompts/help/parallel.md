# Several agents at once

Every agent works in its own git worktree, and the queue is what stops them colliding.

**First, say who you are.** Identity is what attributes every claim, gate outcome and
review, and unasked it is derived from the WORKING TREE — so several agents or subagents
sharing one tree all resolve to the same name, their work merges into one identity, and
a review gate compares an agent with itself and passes. Nothing errors.

    ddflow_identify(agent="reviewer-2")   over MCP: declares it for the connection
    ddflow --agent reviewer-2 ...         one CLI invocation
    DDFLOW_AGENT=reviewer-2               a harness that spawns agents

Innermost wins. One agent per worktree needs none of this; several in one tree need it,
and there is no signal that would let ddflow work it out for them.

    ddflow claim <id>              lease + worktree; exit 3 = refused, with the reason
    ddflow claim <id> --no-worktree   for work that edits nothing

`claim` refuses when another agent holds the item, when a dependency is unmet, when the
item's globs overlap something already in flight, or when the parallelism caps are full.
**The refusal is the feature** — it names the conflicting item and its holder, so the
answer is "take a different one", not "wait and hope".

Two caps, because they are two different statements:

- `schedule.max_parallel_tasks` — how many items may be in flight at once. Every live
  lease counts, worktree or not: a review task occupies an agent just as a coding task
  does.
- `worktree.max_parallel` — how many worktrees may exist. A claim that made no tree
  consumes no disk and does not count against it.

Both are counted across the WHOLE queue, not the slice you asked about.

## The log never conflicts

Each agent appends to its own shard, so two agents writing at the same moment produce no
merge conflict — ever. Order comes from a Lamport clock rather than wall time, because
two machines have two clocks and sorting a merged history by timestamp interleaves them
wrongly.

## When an agent disappears

Its lease expires and its worktree is still there, with its work in it. See
`ddflow help recovery` — nothing is stolen, and nothing is thrown away.
