# Architecture

## The one decision everything else follows from

> **The append-only event log is the source of truth. Everything else is a projection
> that can be deleted and re-derived.**

```
      .ddflow/events/<agent>.jsonl          ← committed, append-only, one file per agent
                   │
              fold() — pure, deterministic
                   │
     ┌─────────────┼──────────────┬────────────────────┐
     ▼             ▼              ▼                    ▼
  State        index.db      docs/ddflow/*.md    RECONSTRUCTION.md
 (in memory)  (gitignored)    (generated)          (generated)
```

Four properties fall out of that inversion, none of which needed to be engineered
separately:

| Property | Why it is free |
|---|---|
| **Merge without conflict** | Each agent appends to its own file. Two branches touch two files. Measured: a real two-branch merge resolves clean, no conflicts ([R2](RESEARCH.md)). |
| **Crash recovery** | State is never *written*, only folded. There is no half-updated record to repair; the last event about an item says exactly where it stopped. |
| **Reconstruction from logs** | Operator prompts are events. Replaying the log replays the decision history. |
| **Tamper-evidence** | Event ids are content addresses. Editing an event changes its id and orphans every reference to it; `ddflow doctor` detects it. |

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
in the metadata tells them apart — only a diff does ([R5](RESEARCH.md)). `ddflow
recover` measures each tree and prints the exact `git diff` to run. It never deletes.

## Dependencies are inherited

`needs` is declared on an item; readiness is evaluated over an item **and all its
ancestors**. This is not a convenience — it is what makes the two-level model mean
anything. A phase is never claimed, because phases complete when their tasks do, so a
phase-level dependency that governs only the phase item governs nothing an agent picks
up. `schedule.inherited_deps` returns `(owner, dep)` pairs so a refusal can say where
the dependency came from; an operator told only "P2.T1 needs P1" goes looking for a
declaration that is not written there.

One dependency is deliberately *not* inherited: one pointing into the item's own
subtree. An umbrella that declares a dependency on its own child would make that child
wait for itself, and a plan typo would become a permanent hang. Beneath an umbrella such
a dependency is satisfied by running, not by waiting.

`plan_blocker` — umbrella, cycle, dependencies — is shared by the scheduler and by
`lease.acquire`. They disagreed for months: `next` withheld an item on its dependencies
and `claim` granted it a worktree a second later, so choosing work by id rather than by
asking bypassed the graph entirely. The lease layer still asks the conflict question
itself, unconditionally, because refusing an overlapping claim is a safety property
rather than a scheduling preference and must not be switchable off by
`schedule.ready_policy`.

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
- **Agent gates** are judgement an LLM performs; ddflow demands the evidence and records
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
3. **Reviewer family is checked, and an unclassified reviewer proves nothing.**
   Same-family reviewers share the author's blind spots, so their agreement is not
   independent evidence — it measures shared priors. `complete` refuses unless at least
   one reviewer came from a different pretraining family. `config.family_for` returns
   `""` for a model it does not recognise, and that is load-bearing: the two former
   implementations returned a non-empty stand-in (one the model's own name, one the
   literal string "unknown"), both of which compared unequal to every real family — so
   a reviewer recorded with no model at all *established* independence. A check built to
   refuse unverified independence must not be satisfiable by the absence of information.
4. **Silence is not an outcome.** With `gates.require_outcome` (default on), every gate
   in the pipeline must carry some outcome before an item completes. Without it,
   `gates.required` held three of ten steps and the other seven could be omitted with no
   trace — which is the same failure as (1), one level up: not a wrong answer, an absent
   one read as a good one. `gate skip --reason` is the auditable way past a step, and it
   names the single step rather than overriding all of them.
5. **A requirement that names nothing is an error.** `gates.required` is enforced by
   intersecting it with the item's pipeline, so naming a gate no pipeline lists made the
   requirement *disappear* instead of raising. `gates.inert_requirements` reports it and
   `complete` refuses — the vacuous-truth class aimed squarely at the anti-vacuous-pass
   mechanism.

## Layers

A module may import only from layers **below** it. `tests/test_layering.py` checks this
by walking the AST, because this package has been bitten three times by a rule that
existed only in prose — two `family_of` implementations, three TOML overlay loaders, and
`mcp_server` reaching into `cli`. A layering rule nothing checks decays into a
dependency graph.

```
surfaces/   cli.py, mcp.py              argparse and JSON-RPC. No policy.
views/      human.py, markdown.py       one renderer per result kind.
services/   queue, gates, review,       the application layer. Both surfaces call it.
            knowledge, sessions,        Nothing here prints.
            leases, health, setup
infra/      log, worktree, proc,        disk, git, sqlite, subprocess, containers, TOML
            store, container, tomlcfg
core/       events, model, schedule,    PURE. No disk, no network, no subprocess.
            progress
config.py                               read by every layer; imports none of them
```

**`core` is pure, and that is load-bearing.** `fold`, the scheduler, the loop detectors
and the `Event` record itself have no I/O, which is why every readiness, gating and
recovery rule is testable without a fixture. The first run of the layering test found
`core.model` importing `infra.events` — for the `Event` dataclass alone. That single
import inverted the dependency the purity rests on, and nobody would have noticed until
a test needed a temp directory to check an arithmetic rule. The fix split the two things
`events.py` had been conflating all along: **`core.events` is the record** (a value, with
a content-addressed id) and **`infra.log` is the store** (append-only, flock-serialised,
per-agent shards).

It also found `infra.container` importing `services.review` to discover reviewer URLs
for its warnings. `infra` owns the container primitives and has no business knowing
reviewers exist; the caller passes them in.

`services` and `views` are declared PEERS rather than stacked: a service may render a
markdown view as its result, and a view reads service types to render them. That is one
layer split by role, and saying so is more honest than an exemption list that grows.

## Module map

| Module | Responsibility |
|---|---|
| `core/events.py` | the `Event` record, Lamport stamp, content-addressed id |
| `core/model.py` | domain types and `fold` — pure, no I/O |
| `core/schedule.py` | readiness, inherited dependencies, glob conflicts, cycles |
| `core/progress.py` | work aggregation and the six loop detectors |
| `infra/log.py` | the append-only log: `flock`, `fsync`, per-agent shards |
| `infra/worktree.py` | git worktree lifecycle, safe merge |
| `infra/proc.py` | every subprocess, with stdin detached — see below |
| `infra/store.py` | SQLite projection + BM25 retrieval (disposable) |
| `infra/container.py` | container detection, loopback rewriting |
| `infra/tomlcfg.py` | one TOML overlay loader, one unknown-key policy |
| `services/queue.py` | the work queue: add, claim, complete, merge |
| `services/leases.py` | acquire/renew/release, crash scanning, salvage advice |
| `services/gates.py` | gate definitions, execution, evidence, independence |
| `services/review.py` | reviewer backends — any LLM, four wire formats |
| `services/sessions.py` | prompt provenance, redaction, replay, bundles |
| `services/companions.py` | detect and register the MCP servers that serve the gates |
| `services/importer.py` | read an existing project's todo/lessons/ADR/research/journal/OptMem corpus and PROPOSE it as a queue; `verify_import` answers whether it is still true and whether anyone finished it |
| `services/help.py` | `ddflow help`: narrative from templates, capability inventory generated from the live tool table |
| `services/adopt.py`, `enforce.py`, `cleanup.py`, `prompts.py` | install, the commit hook, worktree classification, templates |
| `views/markdown.py` | the generated views and the budgeted brief |
| `surfaces/cli.py` | argparse |
| `surfaces/mcp.py` | MCP stdio server |
| `config.py` | documented knobs, TOML + env, and the one family map |

No third-party dependency.

**`infra/proc.py` is 50 lines and exists for one reason.** ddflow runs as an MCP server
over **stdio**: the JSON-RPC session is this process's stdin and stdout.
`subprocess.run(...)` with no explicit `stdin=` hands the child that same pipe, so a
child that reads stdin — an arbitrary shell command in a gate, a reviewer CLI, an `npx`
that wants to prompt — eats the protocol bytes or closes the descriptor. The server then
exits **zero**, with an empty stderr, and the client sees a closed stream with nothing to
explain it. All twelve call sites had this. `proc.run` defaults to `stdin=DEVNULL`, and a
ratchet fails the suite if any module reaches for the stdlib directly.

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
