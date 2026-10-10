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

Events carry a **Lamport counter**; the total order is `(lamport, agent, id)` (`Event.sort_key` in `ddflow/core/events.py`).

- Lamport gives causality: an event written after observing yours sorts after yours.
- `agent` then `id` break ties deterministically, so two machines folding the
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
surfaces/   cli.py, mcp.py, registry.py,   argparse and JSON-RPC. No policy. A command is
            declared/, parsers/, tools/,   DATA (registry.Command); both surfaces are
            records.py, mcp_protocol.py    generated from it.
api/        lifecycle/, knowledge/,        the application layer: ONE typed entry
            reporting/, items.py,          point per operation. Both surfaces call it.
            gates.py, records.py
services/   gates/, guidance/,             the domain. Every operation returns an
            searchcore/, leases.py,        Outcome. Nothing here prints.
            cmdrunner.py, ticks.py
views/      human.py, markdown.py          one renderer per result kind. PEERS with
                                           services: one layer split by role (below).
infra/      git.py, proc.py, fsio.py,      disk, git, sqlite, subprocess, containers,
            log.py, worktree.py,           TOML, machine-local state.
            tomlcfg.py, localstore.py
core/       events.py, model.py,           PURE. No disk, no network, no subprocess.
            handlers/, defs.py, clock.py,
            graph.py, globs.py, redact.py
config.py, config_sections/                read by every layer; imports none of them
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
| `core/flow.py` | branching model (trunk / gitflow), merge targets, stacking, SemVer and Conventional Commits — pure |
| `infra/log.py` | the append-only log: `flock`, `fsync`, per-agent shards |
| `infra/worktree.py` | git worktree lifecycle, safe merge (into a branch nobody has checked out, via a throwaway tree), push, tags |
| `infra/forge.py` | pull/merge requests through `gh` / `glab` — no token, no HTTP; `ForgeUnavailable` kept apart from a refusal |
| `infra/proc.py` | every subprocess, with stdin detached — see below |
| `infra/store.py` | SQLite projection + BM25 retrieval (disposable) |
| `infra/container.py` | container detection, loopback rewriting |
| `infra/tomlcfg.py` | one TOML overlay loader, one unknown-key policy |
| `api/__init__.py` | the typed operation surface: one entry point per operation, called by both surfaces |
| `api/lifecycle/claim.py`, `wait.py`, `ready.py`, `heartbeat.py`, `complete.py`, `merge.py`, `brief.py`, `reservations.py` | the work queue, one module per area (`api/lifecycle/` re-exports the public names): claim and worktree adoption, wait, next, heartbeat/release, complete, merge, brief, and the reservation queue they share |
| `api/knowledge/lessons.py`, `retrieval.py`, `pairs.py`, `research.py`, `bugs.py`, `bug_close.py`, `regression.py`, `sessions.py`, `memory.py` | what the project remembers, one module per area (`api/knowledge/` re-exports the public names) |
| `api/reporting/overview.py`, `records.py`, `health.py`, `views.py` | read-only questions, one module per area (`api/reporting/` re-exports the public names): status, show, doctor and recover, board and render |
| `services/leases.py` | acquire/renew/release, crash scanning, salvage advice |
| `services/gates/defs.py`, `runner.py`, `testcmd.py`, `evidence.py`, `mutation.py`, `outcomes.py`, `reviewers.py` | gates, one module per area (`services/gates/` re-exports the public names): definitions, execution, the test-command advice, evidence fingerprints, mutation and regression-test verification, recorded outcomes, reviewer independence |
| `services/flow.py` | open a request, `pr sync` (merged / changes requested / closed / approved), version plans and cuts ([R16](RESEARCH.md)) |
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

**Dependencies.** Two runtime dependencies, both pure Python: **Jinja2** (the prompt and
export templates) and **tomlkit** (config write-back that keeps comments and layout),
declared in `pyproject.toml` and decided in `docs/ddflow/decisions/unify.md` (D-unify 1).
Everything else is an optional extra. Each is to be imported in exactly one adapter module
(`importlinter-extras-one-adapter` enforces it) with a standard-library fallback; two adapters
have landed and two extras are declared but not yet used. Where the extra is absent the fallback below runs and the
missing extra is reported (a `ddflow doctor` line); a gate or check that needs the extra and
cannot run is recorded `unavailable`, never passed:

| Extra | Library | The one adapter | Fallback |
|---|---|---|---|
| `ddflow[search]` | rapidfuzz | `core/fuzzy.py` | difflib |
| `ddflow[rag]` | model2vec (sqlite-vec is declared, not yet used) | `services/embed.py` | BM25 only |
| `ddflow[watch]` | watchfiles | not landed yet (declared in `pyproject.toml`) | polling |
| `ddflow[mcp-sdk]` | mcp | not landed yet (declared in `pyproject.toml`) | the stdlib stdio engine |

`services/prompts.py` still carries a standard-library fallback renderer so a stripped
deployment starts; it is a tested, loud degraded path, not a second implementation.

**`infra/proc.py` is 50 lines and exists for one reason.** ddflow runs as an MCP server
over **stdio**: the JSON-RPC session is this process's stdin and stdout.
`subprocess.run(...)` with no explicit `stdin=` hands the child that same pipe, so a
child that reads stdin — an arbitrary shell command in a gate, a reviewer CLI, an `npx`
that wants to prompt — eats the protocol bytes or closes the descriptor. The server then
exits **zero**, with an empty stderr, and the client sees a closed stream with nothing to
explain it. All twelve call sites had this. `proc.run` defaults to `stdin=DEVNULL`, and a
ratchet fails the suite if any module reaches for the stdlib directly.

## Shared interfaces

D-unify replaced one-off implementations with one module per concern. Each row names the
module that owns the concern; a second implementation elsewhere is a bug, and a guard in
`tests/test_architecture_guards.py` (baselines under `tests/guard_baselines/`) or an
import-linter contract counts the stragglers and can only go down.

| Concern | The one module | What it gives |
|---|---|---|
| git | `infra/git.py` | every git call: timeouts, status and path parsing, `GitResult` |
| processes | `infra/proc.py` | `run`, `popen`, `capture` (timeout, optional input, a `Captured` result), `spawn_stdio` (a child whose pipes are spoken to, as a stdio probe does), `spawn_shell`, `kill_group`: stdin detached, process group killed |
| files | `infra/fsio.py` | `atomic_write`, `replace_text`, `file_lock`, `ensure_ignored_dir`, `repo_rel`, managed regions; `scratch_dir` and `temp_text` for temporary directories and files |
| TOML writes | `infra/tomlcfg.py` | `upsert`, `remove`, `move_keys` (tomlkit), the overlay readers; `services/configwrite.py` validates then writes `config.toml` |
| time | `core/clock.py` | the one timestamp parser and writer, durations, ages |
| graphs | `core/graph.py` | closure, cycles, topological order, longest chains, transitive reduction |
| globs | `core/globs.py` | one glob semantics (`match`, `inside`, `overlap`); `core/globspec.py` reads a list |
| redaction | `core/redact.py` | one Redactor with named profiles, used by the log, views, exports and bug reports |
| definition records | `core/defs.py`, `core/records.py` | the managed-definition record every kind shares; the fold's record dataclasses |
| event evolution | `core/upcasters.py` | per-kind payload versions and the upcasters that read old events |
| overlay loader | `services/overlay.py` | `OverlayLoader`: config path, then `.ddflow/<dir>/`, then shipped; eject, drift, validate |
| machine-local state | `infra/localstore.py`, `services/slots.py`, `services/changes.py` | `LocalStore` (read, write, lock, trim `.ddflow/local`; no module outside its own tests has adopted it yet, so the ticks, waits and quota stores keep their own files), counting-semaphore `Slots`, content-based `ChangeDetector` |
| command registry | `surfaces/registry.py`, `surfaces/declared/` | `Command` and `Param`: one declaration generates the argparse parser, the MCP tool, its schema and the parity exemptions |
| CLI executor | `surfaces/cliexec.py`, `registry.CliPolicy` | a declared command with `call` and `render` runs with no `cmd_*` function: `--json` prints the MCP tool's body, a refusal has the tool's lead, the human reads `render`; a `CliPolicy` states what prints differently (the identity as typed, stderr notes, which exits show a result) |
| record surface | `api/records.py`, `surfaces/records.py` | `RecordKind`: the seven verbs (list, show, add, edit, remove, search, revise) on the CLI and MCP |
| MCP protocol | `surfaces/mcp_protocol.py` | which protocol revisions are served and how a reply is shaped for each |
| search | `services/searchcore/`, `services/search.py` | `SearchSource` registry, rankers (`core/rank.py`), bounded regex check; `ddflow search --source` |
| context pack | `services/contextpack.py`, `services/embed.py` | ranked candidates cut to one budget, cited and fenced as data; the one `Embedder` |
| approval | `services/approval.py` | approve-by-digest: `grant`, `check`, `use` |
| command running | `services/cmdrunner.py` | `CommandRunner`: every operator-configured command, with timeouts and an `unavailable` outcome |
| guidance | `services/guidance/` | one engine for rules and decisions: scope, resolve, inject, checks, waivers, review |
| kind pipeline | `config_sections/_kinds.py`, `services/gates/kinds.py` | item kind to gate pipeline, `applies_when`, evidence rules |
| item creation | `services/items.py` | `add_task`: the one way a task is created |
| identity | `services/identity.py`, `core/agentname.py` | who is calling, and what an agent name may contain |
| host | `infra/hostinfo.py`, `infra/signals.py` | this machine's names; load, memory and disk signals |
| tree lifecycle | `infra/worktree.py`, `services/cleanup.py`, `services/tree_owner.py` | create, merge and remove worktrees; classify them; whose tree a command stands in |
| cadence and triggers | `services/ticks.py`, `services/cadence.py`, `services/triggers.py`, `services/schedule.py` | opportunistic periodic work, count-based due passes, event triggers, scheduled definitions |
| digest | `core/digest.py` | the one hashing and content-digest module |
| text | `core/slug.py`, `core/textcut.py`, `core/budget.py` | slugs and safe names, the one clipper, the token and character budget |
| config knobs | `config_sections/_docs.py` | `knob()` and `declare()`: a knob is declared once, on its field |
| compatibility | `services/upgrade_plan.py`, `services/migrations/`, `services/repairs/` | `ddflow upgrade --plan`, versioned migrations and data repairs |

### Where the remaining lines are

Measured at the end of the cleanup (newline count of every `.py` file, `ddflow/` 105,182
lines in 386 modules, `tests/` 123,571 in 582): `services/` 48,021, `api/` 18,409,
`surfaces/` 15,798, `core/` 10,479, `infra/` 7,481, `config_sections/` 2,547, `views/
1,456, `config.py` 966, `__init__.py` and `__main__.py` 25. The cleanup slices changed `ddflow/` by -186 lines and `tests/` by
+3,630 (the pins they added outweigh the helpers they removed), so the interfaces above
removed duplicated *patterns* (the guard baselines fell: deferred imports 534 -> 37,
subprocess calls 13 -> 1, `write_text` 10 -> 0, tempfile 6 -> 0, complexity D-or-worse
100 -> 58) far more than they removed *lines*. The lines are features, not duplication:
ddflow grew from 73,400 lines at the start of D-unify because upgrade, scheduling,
adaptive flow, harness adapters, id schemes, exports and reporting landed beside it.

## Adding a feature as data

Each of these is a declaration; none needs a new branch in a surface, the fold or a
renderer. The guards fail the suite if a change reintroduces the pattern by hand.

- **A command.** Add a `Command(path=..., summary=..., tool=..., params=..., call=..., render=...)`
  to the group's module under `surfaces/declared/` (`GROUPS` gives its help line). The CLI
  parser, the MCP tool with its schema, and the CLI function (`surfaces/cliexec.py`, from
  `call` and `render`) are generated; a command whose output cannot be one `render` of the
  tool's Outcome (stdin, streaming, an exit code that is not the Outcome's) keeps a `cmd_*`
  handler in `surfaces/commands/`; what a command deliberately does
  not do on the other surface is a field (`surfaces/exemptions.py`), not a test table.
  `python -m ddflow.surfaces.tool_table` rewrites the README tool table.
- **A record kind.** Declare a `RecordKind` with `declare(...)` in `api/records.py` (name,
  `def_kind`, fields, columns, filters, verbs). The def kind is registered in
  `core/defs.py` (`DEF_KINDS`), which brings history, provenance, digest, replay and the
  add-time duplicate check. `surfaces/records.py` generates the CLI verbs and MCP tool.
- **A knob.** Add a field with `knob(default, doc=..., choices=..., strictest=...)` inside a
  `@declare("<section>")` dataclass in `config_sections/`. The doc, enum check, strictest
  fallback and `config --explain` row follow; a ratchet fails a knob declared twice.
- **An asset** (a prompt, export template, doctype, schedule). Put the file under
  `ddflow/templates/` and load it through an `OverlayLoader` (`services/overlay.py`) so a
  project can override, eject and validate it; do not add a loader of your own.
- **A tick.** `ticks.register(Tick(name, every_s=..., budget_s=..., target="pkg.mod:fn"))`
  in `services/ticks.py`. It runs from `api._base._load` when due, is budgeted and cut,
  and never raises into the command.
- **A trigger.** Write `.ddflow/triggers/<id>.toml` (event, key, window, debounce,
  cooldown, action); `services/triggers.py` validates and evaluates it and files the queue
  item from its job's template. Count-based due passes belong in `services/cadence.py`.
- **A search source.** `searchcore.register(FuncSource(name, kinds, fn))` returning `Hit`
  rows, as `services/search.py` does for the log, rules, skills, agents, jobs and schedules.
  `ddflow search --source` and the context pack pick it up.
- **A pipeline.** Register an item kind in `config_sections/_kinds.py` (`KindSpec`), or
  set `[gates].kind_pipelines.<kind>` in config; `services/gates/kinds.py` resolves the
  gates, and `[gate.<id>] applies_when` narrows them by the files the item declares.

## What is deliberately absent

- **No daemon.** Every command is a short-lived process. A daemon would be a second
  thing to crash, and its state would be the very thing the log already is.
- **No required embeddings.** BM25 ranked correctly on every probe query including a
  synonym-only one ([R4](RESEARCH.md)). Local embeddings are the optional `ddflow[rag]`
  extra, never downloaded at runtime, with BM25 as the fallback.
- **No MCP SDK in core.** The stdio transport is stdlib JSON-RPC (`surfaces/mcp_protocol.py`).
  The official SDK is declared as the optional `ddflow[mcp-sdk]` extra for a future network
  transport; no module imports it yet.
- **No incremental projector.** An incremental updater is a second implementation of
  `fold` that can disagree with it, and a cache that silently disagrees with its source
  is worse than no cache. `rebuild` drops and re-derives; at 407k events/s it can afford to.
- **No third hierarchy level.** Phase → task, plus dependencies that may cross phases.
  Anything deeper is representable by a cross-phase dependency and costs more in
  bookkeeping than it buys.
