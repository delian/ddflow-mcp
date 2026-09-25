# Several agents at once

Every agent works in its own git worktree, and the queue is what stops them colliding.

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
