# Architecture

## The one decision everything else follows from

> **The append-only event log is the source of truth. Everything else is a projection
> that can be deleted and re-derived.**

```
      .orchard/events/<agent>.jsonl          ← committed, append-only, one file per agent
                   │
              fold() — pure, deterministic
                   │
     ┌─────────────┼──────────────┬────────────────────┐
     ▼             ▼              ▼                    ▼
  State        index.db      docs/orchard/*.md    RECONSTRUCTION.md
 (in memory)  (gitignored)    (generated)          (generated)
```

Four properties fall out of that inversion, none of which needed to be engineered
separately:

| Property | Why it is free |
|---|---|
| **Merge without conflict** | Each agent appends to its own file. Two branches touch two files. Measured: a real two-branch merge resolves clean, no conflicts ([R2](RESEARCH.md)). |
| **Crash recovery** | State is never *written*, only folded. There is no half-updated record to repair; the last event about an item says exactly where it stopped. |
| **Reconstruction from logs** | Operator prompts are events. Replaying the log replays the decision history. |
| **Tamper-evidence** | Event ids are content addresses. Editing an event changes its id and orphans every reference to it; `orchard doctor` detects it. |

The cost is that there is no human-editable surface. That is deliberate. The project this
was extracted from used markdown as its source of truth and needed a dedicated audit
script to reconcile checkbox state against git history — which, when first run, found
~170 of 269 unchecked items had in fact shipped. Markdown that is simultaneously a human
document and a machine record drifts, because humans edit prose and machines edit
structure and neither notices the other. Here the markdown is generated and stamped
`GENERATED`, and a stale view is repaired by regenerating it rather than reconciling it.

## Ordering

Events carry a **Lamport counter**; the total order is `(lamport, agent_id, event_id)`.

- Lamport gives causality: an event written after observing yours sorts after yours.
- `agent_id` then `event_id` break ties deterministically, so two machines folding the
  same set of events get the same answer.
- Wall-clock `ts` is recorded for humans and is **explicitly not the sort key** — clock
  skew between machines sharing one NFS checkout would otherwise reorder history.

The clock is read from the *tail* of each shard rather than by re-reading everything:
a shard has exactly one writer, so its Lamport values are non-decreasing and the last
line carries its maximum. That makes an append O(shards), not O(events) — which matters
because the probe measured NFS at ~36× the cost of local I/O.

## Concurrency

Two mechanisms, deliberately at different layers.

**`flock` protects the log.** Appends are serialised by an exclusive lock on one file,
then `fsync`ed. POSIX `O_APPEND` atomicity is *not* relied on: NFS has no append
operation, so the client does seek-then-write and two appends can interleave. The lock
file is never atomically replaced — renaming over a lock puts two holders on two inodes,
each believing it is exclusive.

**Leases coordinate agents.** A lease is an *event with an expiry*, not a lock file. A
lock file must be deleted by its holder, so a killed holder leaves one nobody can safely
remove; a lease simply stops being renewed, and expiry is then a fact any observer can
compute. Acquisition is check-then-act and therefore runs inside a transaction that
spans both halves — without which two agents both read "free" and both claim. (The
stress test found the same class one level up: `acquire` did not refuse an *already
finished* item, so an agent with a stale snapshot re-did completed work — 50 claims for
32 tasks. See [R6](RESEARCH.md).)

**Expiry never steals.** `lease.reclaim_policy` defaults to `report`, because a crashed
agent's worktree is sometimes irreplaceable and sometimes a superseded draft, and nothing
in the metadata tells them apart — only a diff does ([R5](RESEARCH.md)). `orchard
recover` measures each tree and prints the exact `git diff` to run. It never deletes.

## Conflict detection

Two agents are prevented from editing one file by *declared globs*, compared with a
deliberately over-eager overlap test. A false positive costs one unnecessary re-order; a
false negative costs two agents editing one file and one of them silently losing a day.
The asymmetry is not close, so the comparison errs toward "yes".

A refusal always names what the refused agent could take **instead**. That is the
difference between a blocked agent and a re-ordered one, and it is why `claim` returns
exit 3 with alternatives rather than simply failing.

## The gate pipeline

A gate is one checkpoint with one outcome. Two kinds:

- **Command gates** have a shell command; the exit code decides.
- **Agent gates** are judgement an LLM performs; Orchard demands the evidence and records
  the answer.

Agent gates are where a workflow rots, because "I reviewed it" costs nothing to say.
Three mechanisms push back:

1. **`unavailable` is a first-class outcome.** A reviewer whose endpoint was down
   approved nothing. It gets its own outcome, its own exit code (2), and appears in the
   completion report as a coverage gap. Collapsing it into either pass or fail is how an
   entire review silently disappears — and the inverse error is just as bad: the first
   implementation here classified a *missing binary* (exit 127) as `failed`, so a linter
   nobody installed looked like a linter reporting problems.
2. **Evidence is required** for gates listed in `gates.evidence_required`. A record with
   no command, no exit code and no output digest is rejected at the API boundary.
3. **Reviewer family is checked.** Same-family reviewers share the author's blind spots,
   so their agreement is not independent evidence — it measures shared priors.
   `complete` refuses unless at least one reviewer came from a different pretraining
   family.

## Module map

| Module | Responsibility | Lines |
|---|---|---|
| `events.py` | append-only log, Lamport clock, `flock`, content addressing | 329 |
| `model.py` | domain types and `fold` — pure, no I/O | 362 |
| `schedule.py` | readiness, dependencies, glob conflicts, critical path | 243 |
| `lease.py` | acquire/renew/release, crash scanning, salvage advice | 308 |
| `worktree.py` | git worktree lifecycle, safe merge | 265 |
| `gates.py` | gate definitions, command execution, evidence, independence | 400 |
| `store.py` | SQLite projection + BM25 retrieval (disposable) | 262 |
| `session.py` | prompt provenance, redaction, replay, bundles | 274 |
| `render.py` | generated markdown views and the budgeted brief | 254 |
| `cli.py` | the portable surface every agent drives | 972 |
| `mcp_server.py` | MCP stdio server over the same functions | 441 |
| `adopt.py` | one-command install into any project | 153 |
| `config.py` | 43 documented knobs, TOML + env | 302 |

`fold` is pure and does no I/O, so every scheduling, gating and recovery rule is testable
without a disk. `cli.py` is the only module that prints. Total: **4579 lines** of standard-library Python, no third-party dependency.

## What is deliberately absent

- **No daemon.** Every command is a short-lived process. A daemon would be a second
  thing to crash, and its state would be the very thing the log already is.
- **No embeddings.** BM25 ranked correctly on every probe query including a synonym-only
  one ([R4](RESEARCH.md)). `lessons.search_backend` exists for when that stops being true.
- **No MCP SDK dependency.** The stdio transport is ~200 lines of JSON-RPC. A portability
  tool that only installs where a package index is reachable is not portable.
- **No incremental projector.** An incremental updater is a second implementation of
  `fold` that can disagree with it, and a cache that silently disagrees with its source
  is worse than no cache. `rebuild` drops and re-derives; at 407k events/s it can afford to.
- **No third hierarchy level.** Phase → task, plus dependencies that may cross phases.
  Anything deeper is representable by a cross-phase dependency and costs more in
  bookkeeping than it buys.
