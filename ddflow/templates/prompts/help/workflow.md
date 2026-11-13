# The workflow

## Where work comes from

A **phase** groups **tasks**. A task can be split into sub-tasks in place with
`ddflow split`, keeping its id and its history — which is what you do when a task turns
out to be two concerns, rather than abandoning it and filing two new ones.

Two things make an item safe to hand out, and both are declared on it:

- **`--needs`** — the ids it waits for. Inherited: a phase's dependency governs every
  task inside it, so you declare it once. `ddflow task add` (and `ddflow update
  --needs`) refuses an id that does not exist — `no such id`, with a "did you mean"
  when one is close — so a typo surfaces straight away instead of as work waiting on a
  ghost. An imported queue is the one exception: a dependency nothing produced is kept
  as a dangling need that stays unmet, deliberately.
- **`--globs`** — the files it writes. This is what lets two agents run at once:
  `ddflow claim` refuses an item whose globs overlap something already in flight. A
  task with no globs is one the conflict detector cannot protect.

## Pre-file triage

Before you add a task or a bug, run this checklist first:

1. Check existing tasks and bugs for a duplicate or a closable overlap.
2. Check what is already planned.
3. Identify reusable, extensible core modules.
4. Reuse prior research and recorded best practice — never re-derive it.
5. Ask the operator only for person-decisions.
6. File it with a `wave` tag (`no-deps` when it has no prerequisite), its `--needs`,
   and the right phase — create one if none fits.

## Picking work up

`ddflow next` shows only what may actually start: dependencies met, no one else
holding it, no file conflict, under the parallelism cap. Exit 2 means nothing is
actionable — which is an answer, and usually means something is blocked rather than
that the queue is empty. `ddflow next --json` gives the same thing with reasons.
Slice it by label: `ddflow next --tag TAG` (repeat for several, all must match; any
`ns:value` label), `--wave N` for tag `wave:N`, and `--phase ID`, alone or together. A tag
no item carries is refused (exit 1) with the nearest ones, never an empty answer.

With the parallelism cap full, `next` still says what is otherwise unblocked: items held
ONLY by the cap are listed under "Ready but held by the cap" (JSON `held_by_cap`, with
their globs). Exit is 0 then (the one case where the exit code changed); it stays 2 only when nothing is ready and nothing is held. A named `ddflow claim <id>` is not capped, so it grants
one now; `ddflow next --ignore-cap` offers them as ready for the call. Merged items that
still need `ddflow complete` are their own list (JSON `needs_complete`), and
dependency-blocked items are a count unless `--blocked`.

`ddflow claim <id>` takes a lease and creates a git worktree for it. Leases expire, so
a crashed agent's work is reclaimable; `ddflow heartbeat <id>` renews one during long
work. `ddflow release <id>` gives it up without completing.

## Putting it down

`ddflow complete <id>` is the gate. It refuses — exit 3 — and lists every unsatisfied
requirement rather than making you guess. Then `ddflow merge <id>` lands the branch,
run from the primary checkout.

See also: `ddflow help gates`, `ddflow help parallel`.
