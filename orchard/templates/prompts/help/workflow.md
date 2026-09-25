# The workflow

## Where work comes from

A **phase** groups **tasks**. A task can be split into sub-tasks in place with
`orchard split`, keeping its id and its history — which is what you do when a task turns
out to be two concerns, rather than abandoning it and filing two new ones.

Two things make an item safe to hand out, and both are declared on it:

- **`--needs`** — the ids it waits for. Inherited: a phase's dependency governs every
  task inside it, so you declare it once. An id that does not exist is treated as
  UNMET, deliberately, so a typo surfaces as blocked work rather than as work that
  starts early.
- **`--globs`** — the files it writes. This is what lets two agents run at once:
  `orchard claim` refuses an item whose globs overlap something already in flight. A
  task with no globs is one the conflict detector cannot protect.

## Picking work up

`orchard next` shows only what may actually start: dependencies met, no one else
holding it, no file conflict, under the parallelism cap. Exit 2 means nothing is
actionable — which is an answer, and usually means something is blocked rather than
that the queue is empty. `orchard next --json` gives the same thing with reasons.

`orchard claim <id>` takes a lease and creates a git worktree for it. Leases expire, so
a crashed agent's work is reclaimable; `orchard heartbeat <id>` renews one during long
work. `orchard release <id>` gives it up without completing.

## Putting it down

`orchard complete <id>` is the gate. It refuses — exit 3 — and lists every unsatisfied
requirement rather than making you guess. Then `orchard merge <id>` lands the branch,
run from the primary checkout.

See also: `orchard help gates`, `orchard help parallel`.
