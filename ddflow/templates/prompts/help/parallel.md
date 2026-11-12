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

`claim` refuses when another agent holds the item, when a dependency is unmet, or when
the item's globs overlap something already in flight. The parallelism cap does NOT hold
a claim for a NAMED item: `claim <id>` grants it whatever the cap says. The cap decides
only which items `next` OFFERS in the ready list, and names the rest as held by the cap.
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

How many items may be in flight is the **parallelism limit**, set by `schedule.parallel`:

- `auto` (the default) — the limit adapts between `schedule.max_parallel_min` (2) and
  `schedule.max_parallel_max` (8), starting at `schedule.max_parallel_tasks` (4). It rises
  by one after ten healthy minutes with the limit actually reached, and falls by a quarter
  when a signal (load per core, memory or disk pressure, reviewer latency, gate or merge
  failures) is bad on 3 of the last 4 samples. Samples are taken as commands run -- there
  is no daemon -- and kept in the git-ignored `.ddflow/local/flow/`; the limit is derived
  on this machine and never committed.
- `fixed` — `schedule.max_parallel_tasks` is the limit, exactly as before auto existed.

Every live lease counts, worktree or not: a review task occupies an agent just as a coding
task does. A shrink never touches a running lease; it only stops new admissions.
`worktree.max_parallel` caps how many worktrees may exist: 0 (the default) follows the
limit -- it is not unlimited -- and a number is an independent cap. A claim that made no
tree does not count against it. Both are counted across the WHOLE queue, not the slice you
asked about.

`schedule.reserved_slots` (default 0) keeps that many slots of the limit for bug fixes and
tasks tagged `schedule.reserved_tags` (default `ddflow-gap`): ordinary work holds at most the
limit minus it, a reserved slot with no such work stays free, and reserved work may also use
any ordinary slot. One slot of a cap always stays ordinary. `next`, `status` and `brief` say
how many are reserved and for what.

`ddflow status` and `ddflow brief` print the limit in force on one line: `parallel: 6
(auto: ceiling 8; limited by load per core)` or `parallel: 4 (fixed)`. "limited by
independent work" means every ready item that can run beside what is in flight is already
offered: more agents would have nothing to do.

    ddflow config schedule.parallel fixed                 back to a fixed number
    ddflow config --local --set schedule.max_parallel_max 6   this machine's ceiling

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

## Whole suites queue, and wait for load

Full test suites share one host-wide lock (`[ci].suite_wait_s` is how long a run waits). The
number that may run at once is `[suites].max_concurrent`; 0 (default) derives it from the cores
(`[suites].cores_per_suite` each, never under `[ci].max_concurrent_suites`). A second suite is
also held while the host load is over `[suites].load_ceiling_per_core` (a lone suite never is),
and the wait says why and shows the load. The wait is queue time: the per-test timeout starts
when the suite is admitted, never before.

The queue is not plain first in, first out: a fix of red main and the merge-queue integrator go
first, then a phase's closing run, then an item's run, then a baseline measurement; within a class
the shortest predicted run (the median of the measured runs of its kind) goes first. A run that has
waited `[suites].max_wait_factor` times its own cost goes first whatever arrives, and one agent
leads at most `[suites].agent_share` of the waiting runs. The wait line reads `you are #4 in the
queue, estimated wait 14m (3 ahead, predicted 31m)`. A class is read from the holder label by
`[suites].class_patterns` unless the caller names one.

## The log never conflicts

Each agent appends to its own shard, so two agents writing at the same moment produce no
merge conflict — ever. Order comes from a Lamport clock rather than wall time, because
two machines have two clocks and sorting a merged history by timestamp interleaves them
wrongly.

## When an agent disappears

Its lease expires and its worktree is still there, with its work in it. See
`ddflow help recovery` — nothing is stolen, and nothing is thrown away.

## A gate result is reused for the same tree

A `ci` or `unit_tests` command gate that already ran on the same code tree, command and
environment is not run again: it is recorded with the same outcome and the evidence `cached from
run <key> of <time>` (`cached: true`, the original seconds in `cached_elapsed_s`). The key is the
tracked files minus `.ddflow/` and `[gates].doc_globs` documents (a dirty tree by its content),
the gate, the command, the Python version, the dependency lock files and `[resultcache].env_vars`;
any change misses. Only `passed` and `failed` are stored, never `unavailable` or `partial`; a
stored `failed` is served only with `[resultcache].reuse_failed` (a rerun is how a flake is told
from a failure); an entry that does not read back whole is a miss. The cache is per project under
`.ddflow/local/resultcache`, bounded by `[resultcache].keep_days` and `max_mb`;
`[resultcache].enabled = false` turns it off.

## A full pass survives main moving when the move reaches no test

A phase closes on a full-suite pass newer than its last merge. When main moved since the pass (it
records the commit it ran on), `ddflow complete` asks `revalidate.assess`: the paths that differ,
minus `.ddflow/` and documents, are looked up in the test closure. None reached: the pass still
counts, recorded as a pass with the note `unchanged by delta`. Some reached with no hub: only those
tests are needed, recorded as `full on A plus selected on delta B` (partial when the selection
fell back, never a silent pass). A hub or test infrastructure in the delta, or git or the selector
unable to say: the full run is needed.
