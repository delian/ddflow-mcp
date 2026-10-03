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

When there is no different one, wait on the event rather than on a timer:

    ddflow wait --item <id>        sleeps until <id> can be claimed; exit 0 = claim now
    ddflow wait                    ... until anything is ready

It watches the log and returns the moment the holder completes, releases or lets its
lease lapse, naming what freed it. Run it as a background process and the harness wakes
the agent when it exits — no polling, and no person saying "try again". Exit 2 means
the deadline passed, or that waiting cannot help (a cycle, an operator's hold, a
dependency nobody is working on), with what to do instead. The holder hears about it
too: `heartbeat` lists who is waiting on its item, and `release` / `complete` name the
agents they woke.

Two caps, because they are two different statements:

- `schedule.max_parallel_tasks` — how many items may be in flight at once. Every live
  lease counts, worktree or not: a review task occupies an agent just as a coding task
  does.
- `worktree.max_parallel` — how many worktrees may exist. A claim that made no tree
  consumes no disk and does not count against it.

Both are counted across the WHOLE queue, not the slice you asked about.

**What `next` offers never overlaps itself.** The free slots are filled in priority order,
but an item whose globs overlap one already offered in the same answer is not offered with
it (the second claim would be refused): it stays queued, blocked as `conflict` with
"globs overlap <id> ... offered in this plan", and the next independent item takes its
slot. It is not held by a cap, so `status` counts it separately ("N overlap an offered
item" against "N held by the parallelism cap"), and it becomes offerable once the item it
overlaps is done or released. Globs listed in `shared_globs` overlap nothing. When no two
ready items overlap, the offer is exactly the priority order.

When the ready set is larger than the free slots, **bug fixes get them first**
(`schedule.bugs_first`, on by default): a task tagged with one of `flow.bugfix_tags` or
`flow.hotfix_tags`, or named by an open bug record, is offered before any feature, and
priority orders each group. `bugs_first = false` orders by priority alone.

## The log never conflicts

Each agent appends to its own shard, so two agents writing at the same moment produce no
merge conflict — ever. Order comes from a Lamport clock rather than wall time, because
two machines have two clocks and sorting a merged history by timestamp interleaves them
wrongly.

## When an agent disappears

Its lease expires and its worktree is still there, with its work in it. See
`ddflow help recovery` — nothing is stolen, and nothing is thrown away.
