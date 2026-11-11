# The workflow, the model and the pipelines

How work moves: rules, plan mode, the model, the task and phase pipelines, approval, cadences, jobs, loops, pins, and what is automated. Part of the ddflow [README](../../README.md); the reference moved out of it by D-readme-split. Every heading here keeps the anchor it had in the README.

## The workflow, and changing it

```console
$ ddflow workflow
# The workflow this project runs

   1. research      agent
   2. rules         agent
   3. implement     agent       (required)
   4. lint          command     (required, NOT proven able to fail)
      $ ruff check .
   ...

## The rules, and where each came from

  gates.require_outcome                  True              [default]
  gates.enforce_order                    block             [file]
  schedule.parallel                      auto              [default]
  schedule.max_parallel_tasks            4                 [default]
```

One answer to "what are the rules here": every gate in order, which are commands and
which you perform, which are required, which need evidence, which need a
different-family reviewer, which have been **proven able to fail** — plus the
completion rules, the caps, the reviewers, and **where each value came from**, so a
deliberate choice is distinguishable from a default nobody touched.

### Changing it

```console
$ ddflow workflow gate lint --command "ruff check ." --into task --after implement --required
$ ddflow workflow pipeline task research,implement,lint,unit_tests,merge
$ ddflow workflow drop dedupe
```

All four reach MCP — `ddflow_workflow`, `ddflow_workflow_pipeline`,
`ddflow_workflow_gate`, `ddflow_workflow_drop` — so an agent can change the workflow
*with the operator's agreement*. Their descriptions say to ask first and offer
`dry_run`, because a pipeline governs every future item, not the one in hand.

**A pipeline per item kind.** One registry (task, phase, bug, doc, research, job) maps an
item's kind to the pipeline it runs; each starts on `gates.task_pipeline` (a phase on
`gates.phase_pipeline`), and `gates.kind_pipelines` replaces it for one kind, e.g.
`{ doc = ["implement", "merge"] }`. A promotion task keeps `gates.promotion_pipeline`.
`ddflow workflow` lists every kind that runs something other than the task pipeline (a
phase: other than the phase pipeline), and `workflow --json` (and the `ddflow_workflow`
MCP tool) carries `kind_pipelines`, every kind's effective pipeline; `workflow drop` and the undefined-gate and `applies_to`
checks cover these pipelines too.

Nothing is written until it is checked, and the order is the point: compose the change,
validate the **result**, then replace the file atomically.

- **Keys are plain keyboard keys** (decision D-plain-keys). Every key `config --set`,
  `ddflow_configure` and the `workflow` commands accept is dot-separated ASCII letters,
  digits, `_` and `-`; a quoted segment, a `\uXXXX` escape, whitespace or non-ASCII is
  refused with exit 3, naming the plain spelling when there is one (`use
  gate.unit_tests.command`). A gate id follows the same rule -- it names the
  `[gate.<id>]` section, and a dot in it would nest a table (`a.b` defined gate `a`).
  `config --append-toml` and `ddflow_configure toml` hold every key of the block to the
  same rule -- a table header, a key, an inline-table key: `[flow]` then `"tag_prefix" =
  "v"` is refused with `use flow.tag_prefix` -- and `companions add` refuses a companion
  id that is not one bare key (it names the agent's `[mcp_servers.<id>]` entry).
  Hand-written TOML files are still read as TOML.
- **A pipeline naming an undefined gate is refused**, naming the near miss. That one is
  otherwise silent and permanent: the outcome folds to empty, completion refuses it
  forever, and `gate record` rejects the id as unknown — so the item can never be
  completed at all, and nothing says why.
- **An unknown section or knob is refused**, with a suggestion. `[gatez]` is valid TOML
  and used to be written happily, breaking every later command — the write path
  validated the merged text for *syntax* and then validated the config already on
  *disk*, which is a writer checking the state it is replacing.
- **Dropping a gate takes it out of `required` too**, or it becomes a requirement that
  quietly requires nothing.
- **What is required is `[gates].required`, only.** `required = true` inside a
  `[gate.<id>]` table used to be accepted and ignored, so the gate was never enforced (and
  `required = false` left a listed gate required). A table that disagrees with the knob is
  now warned about on every command (and refused in ddflow's own tree), naming the edit to
  `[gates].required` that does what it says; one that agrees is redundant and passes.

`ddflow workflow` and `ddflow doctor` both re-run those checks against what is on
disk. Everything is a file you can also edit by hand: gates in `[gate.<id>]`, reviewers
in `[[reviewer]]`, companions in `.ddflow/companions.toml`, and every prompt —
including the instructions your agent receives at connect — under `.ddflow/prompts/`.

**One caveat with MCP:** the connection instructions are computed once, when the server
starts. A workflow changed mid-session is live for every tool call immediately, but the
text the agent was handed is stale. Tell it to call `ddflow_workflow`, or restart.

---

## Pin files regenerate; they are never hand-merged

A golden snapshot, a pinned CLI transcript or a generated table changes whenever a knob,
command or event kind does, so every parallel branch touches the same few files. ddflow
re-records them instead of leasing or merging them by hand. A project lists its pin kinds
as data in `.ddflow/pins.toml` (`[pins].recipes_file`), one `[[pin]]` per kind with
`files`, `check` (exit 0 = current) and `regen` (re-records only what differs):

    ddflow pins regenerate --check      # list the stale pins, write nothing (exit 1 if any)
    ddflow pins regenerate              # re-record exactly the stale ones
    ddflow pins regenerate --only NAME  # one pin
    ddflow pins regenerate --install    # optional: the git merge driver + post-merge hook

`--install` (also run by `ddflow adopt` when the recipes file exists) writes
`<file> merge=pinsregen` to `.gitattributes`, sets `merge.pinsregen.driver` and installs a
`post-merge` hook: on a conflict in a pin file the driver keeps our side, the hook then
regenerates the stale pins and folds them into the merge commit (`[pins].after_merge` =
`amend`, `leave` or `off`). List the pin files in `[lease].shared_globs` so a lease on them is
not exclusive. A recipe never runs under xdist.

The hook never makes a merge wait for a test suite. It runs only the recipes that allow it
(a `[[pin]]` with `after_merge = false`, the golden-snapshot and transcript suites in this
repository, is left to an explicit `ddflow pins regenerate`), bounds each command by
`[pins].after_merge_timeout_s`, and reports a recipe it could not finish as unavailable, never
as failed. Two re-recordings never overlap in one tree: the second is unavailable.

## Finding a test polluter

A test that passes alone and fails only after others have run is being polluted: a leaked
environment variable, a module-level cache, a file left behind. `ddflow bisect` finds which
earlier file does it (illustrative output):

```console
$ ddflow bisect tests/test_report.py::test_totals --cmd 'pytest -q -p no:randomly {tests}'
victim: tests/test_report.py::test_totals
candidates before it: 212

Run before the victim, these make it fail (remove any one and it passes):
  tests/test_cache.py

9 run(s)
```

It confirms the victim passes alone and fails after every candidate, then delta-debugs
(Zeller's ddmin) the candidates to a **1-minimal** set -- usually one file, two when the
pollution needs an interaction -- in about `2 * log2(n)` runs. Candidates are the files
that sort before the victim's (`--glob`, default `tests/**/test_*.py`) or `--candidates a,b`
in the order given.

**It is driven by your command, not by ddflow running your tests.** The original idea
assumed ddflow owned test execution; it does not, and building that means understanding every
test framework. The only requirement is a command that runs a list of tests and exits
non-zero on failure, with `{tests}` where the list goes (`pytest -q {tests}`,
`go test {tests}`, `npm test -- {tests}`). Turn off test-order randomisation in it, or a
shuffled victim will not reproduce. The search never looks inside a test.

Probes are three-valued, like every check here: a run that could not be made (timeout, a
command that will not spawn) is **unavailable** and ends the search with exit 2; it is never
counted as a pass or a fail. Exit 2 also covers "the victim fails alone" (not an ordering
problem), "it passes after every candidate" (flaky, or not about earlier files) and an
exhausted `--max-runs` (the smallest set so far is printed). `--repeat N` runs each probe N
times and counts any failure, for a pollution that shows one run in three.

## From plan mode to the queue

Agents plan well and forget reliably. A plan that lives in a chat transcript is gone at
the next session; a plan in the queue survives, fans out to parallel agents, and carries
its own gates.

Tell the agent, at the end of planning:

```
Put that plan in ddflow before you build any of it. One phase for the whole plan, one
task per independently-shippable step. Declare each task's globs — the files it will
write — and its needs, the tasks that must finish first. Then show me `ddflow next`.
```

A glob names files as they are when the task is filed. When a module later becomes a
package (`api/reporting.py` split into `api/reporting/`), an open task's lease on the old
name covers none of the new files, and the conflict detector cannot see the overlap:
`ddflow doctor` notes each such glob with the `ddflow update <id> --globs ...` that
corrects it.

What the agent does with that:

```console
$ ddflow phase add P3 --title "Rate limiting"
$ ddflow task add P3.T1 --phase P3 --globs 'limiter/**'      --title "token bucket"
$ ddflow task add P3.T2 --phase P3 --globs 'api/middleware/**' \
      --needs P3.T1 --title "wire it into the request path"
$ ddflow task add P3.T3 --phase P3 --globs 'docs/**' --needs P3.T2 --title "document it"
$ ddflow next
Ready (1 ready, 0 running, 2 blocked):
  P3.T1  token bucket
      writes: limiter/**
  (blocked) P3.T2: deps — P3.T1 is open
  (blocked) P3.T3: deps — P3.T2 is open
```

**The two fields that do the work are `--globs` and `--needs`.** Globs are how two agents
are stopped from editing the same file: `claim` refuses an item whose writes overlap one
already held, and names what to take instead. Needs are how ordering is enforced without
anyone remembering it. A plan whose tasks declare neither is a list, not a queue — it
will *look* parallel and then two agents will fight over one file.

`ddflow split <id> --into a,b,c` exists for when a task turns out to be three, which is
the normal case rather than a failure of planning.

---

## The model: phases, tasks, dependencies, globs

```
Plan ──► Phase ──► Task
```

A **phase** is a unit of *review*: its own research, its own whole-suite test pass, its
own live smoke run, merged as one coherent feature. A **task** is a unit of *execution*:
one agent, one worktree, one pipeline, one merge. Both carry `needs` (dependencies, which
may cross phases) and `globs` (the files they will write).

```sh
ddflow phase add P2 --title "Billing" --needs P1
ddflow task add P2.T1 --phase P2 --title "invoice model"  --globs "src/billing/invoice.py"
ddflow task add P2.T2 --phase P2 --title "tax rules"      --globs "src/billing/tax.py"
ddflow task add P2.T3 --phase P2 --title "checkout wiring" --needs "P2.T1,P2.T2" \
                                                            --globs "src/checkout/*"
```

**Declare globs.** They are what lets two agents work at once safely. A task with no
declared globs is a task the conflict detector cannot protect.

**Dependencies are inherited.** A phase is never claimed — only its tasks are — so
`P2 needs P1` has to govern everything *inside* P2, or it governs nothing that anyone
picks up. The readiness rule therefore consults an item's ancestors as well as itself:

```console
$ ddflow next
Ready (1 ready, 0 running, 1 blocked):
  P1.T1  money
  (blocked) P2.T1: deps — phase P1 has 3 open task(s) (inherited from P2)
```

The refusal names *where* the dependency came from, because an operator told only
"P2.T1 needs P1" goes looking for a declaration that is not written there. The one
dependency **not** inherited is one pointing into your own subtree or at one of your own
ancestors: an umbrella that declares a dependency on its own child would otherwise make
the child -- and that child's sub-tasks -- wait for something that closes only after
they do, turning a plan typo into a permanent hang.

`ddflow claim` asks the *same* predicate `ddflow next` does. They used to disagree —
`next` withheld a task on its dependencies and `claim` handed out a worktree for it a
second later — so an agent picking work by id rather than by asking bypassed the
dependency graph entirely.

There is deliberately no third level *of kind*: a sub-task is a task whose parent is a
task, so depth is unlimited while the rules stay one set.

---

### Model-tier hint (advisory)

Tag a task `tier:fast`, `tier:balanced` or `tier:deep` (`ddflow task add ... --tags tier:fast`)
to say what kind of model suits it: a cheap one for mechanical bulk work, a balanced one for
implementation, a top-tier one for architecture trade-offs. `ddflow next` (CLI text, `--json`
as a `tier` field, and the bounded `ddflow_next` body) and `ddflow brief` (the item's header and
the ready list) show it; an untagged item shows nothing extra. A harness that dispatches a
subagent may map it to its model choice (see the driver, `docs/ddflow/drivers/implement-phase.md`).
It is advice only: it never affects scheduling, gates or reviewer independence, and an unknown
value such as `tier:foo` is ignored and reported by `ddflow doctor` as a note, not an error.

### Pull dispatch: `ddflow next --lane NAME`

`ddflow next --lane NAME` answers an idle lane with the best item for it: the scheduler core's
pick (`[swarm]` weights and hard constraints) among everything ready, within the parallel limit.
It only reads. The answer carries a one-line dispatch note with the figures and reasons; the
lane records it by claiming with `ddflow claim <item> --note "<note>"`, the command the text form prints.

NAME is a `[template.<name>]` in `swarm.launch_templates_file`. Family and trust come from that
template and the model catalog, and nothing is defaulted: with no template, a template a person
has not approved at its digest, no declared `trust`, or a family or model the catalog cannot
vouch for, the answer is `unavailable` and offers nothing (a locally named model needs its row
in `.ddflow/local/models.toml`). A person approves a template at its current digest with
`ddflow lanes approve NAME` (refused under an agent identity). Exit 0 is an offer; exit 2 is
`unavailable`, `hold` (the parallel limit or paused admission) or `none` (nothing eligible, with
the first `swarm.dispatch_why_items` refusals). It starts nothing: launching is a separate step.

## Work that changes shape while you do it

Tasks can be added at any time, including while their parent is being worked — mid-task
discovery is the normal case, not an exception, and a queue that cannot absorb it pushes
the work into someone's head.

**Sub-tasks are just tasks whose parent is a task.** Not a separate concept with its own
rules: a sub-task declares its own globs, carries its own dependencies, is claimed by its
own agent, and runs in parallel with its siblings when nothing links them — exactly like
any other task.

```sh
ddflow task add P1.T1a --parent P1.T1 --globs "src/parse.py"
ddflow split P1.T1 --into "P1.T1a=parse input" --into "P1.T1b=write records"
```

`split` works **in place**: the original keeps its id, its lease history and everything
recorded against it, and becomes an *umbrella* that completes when its children do.
Closing it and opening two new ones instead would lose the thread between what was
planned and what happened — which is exactly what `ddflow replay` needs.

An umbrella is never offered as ready (its children are), and cannot complete while any
descendant at any depth is unfinished. An *abandoned* child counts as settled, so a
sub-task you decide against does not hold its parent open forever.

**Becoming an umbrella releases the lease**, however you get there — by `split`, or by
adding the first sub-task to a task you are already working. An umbrella holding a live
claim on globs that overlap every child's means a second agent cannot take one of those
children, and crash recovery points at a worktree where nothing further will happen.
`split` already did this; `task add --parent` did not, which is the shape of bug worth
naming: one transition, two ways in, guarded on one.

## Resources and long-running jobs

Globs keep two agents out of one file. Work that **runs** on something — GPUs, a model
server, a shared fleet — declares that too, and the same refusal applies:

```sh
ddflow update TRAIN.3 --resources gpu:6            # or **Resources:** gpu:6 in the plan
ddflow claim TRAIN.3                                 # refused (exit 3) if it does not fit
```

`[schedule] resources = ["gpu=8", "vllm-fleet=1"]` sets capacities; a resource named
nowhere is exclusive. Every live claim counts, the claimant's own included — one agent
starting two 8-GPU runs overcommits the box just the same — and `next` withholds what
does not fit, saying who holds what.

The run itself is a **job**:

```sh
ddflow job run TRAIN.3 "uv run main.py train -c cfg.toml"   # detached; survives you
ddflow job list        # running | exited N (from its log) | gone (killed) | elsewhere
ddflow job end J3f2 --note "loss 0.12, ckpt in out/"         # refused while it runs
```

A launched job runs in the item's worktree, in a session of its own (it outlives the
agent, the MCP server and a restarted remote-control service), and appends its exit code
to its log so a run nobody watched still says how it ended. Liveness is computed, not
stored: a zombie is not alive, and a reused pid is caught by the process start time.
`ddflow job add --pid` registers a process started some other way. Every `brief` lists
jobs not yet recorded as ended — "WAIT, do not start it again" for a running one. Running
jobs and the jobs of the item a brief is about are listed in full; other items' jobs on
another host (they may be running) up to the newest five; their exited or killed jobs
collapse to one count line (a brief about an item -- named, held or suggested -- lists none
of them; one about no item at all, the newest five). So a backlog of uncollected jobs never
pushes the item's own section out of the token budget. `ddflow job list` shows them all.

## Usage meters

Before ddflow can respect a quota it has to know how much of it is gone. The meters answer that from
what is already on this machine, and they never call a vendor's usage endpoint with your credentials.
`ddflow.services.meters` keeps a registry of readers; `read_all` runs them and `best` picks the figure
to act on, preferring a fresh one and marking a stale one as stale.

| Reader | Reads | Gives |
|---|---|---|
| `claude-snapshot` | The last statusline or stream-json `rate_limit_event`, kept in a snapshot file | Vendor percent and reset, 5h and week |
| `claude-cache` | `cachedUsageUtilization` in `~/.claude.json` | Vendor percent and reset, 5h and week |
| `claude-tokens` | Claude Code transcripts, subagents included, once per (message.id, requestId) | Tokens per window |
| `kilo` | Kilo's message rows, opened read-only | Tokens and USD per window |
| `ddflow-sessions` | `session.started` in this project's log | Sessions per window, this project only |

Each reading carries its `source`, `observed_at` and a `stale` flag. A percent is stale after
`quota.stale_after_s`, or once its window has reset. **A reading that cannot be taken is `unavailable`
with a reason, never zero**, and the type refuses anything else. A measured zero is still a zero: the
source was read and nothing happened in the window. Three cases read as zero at a glance and are not:

- a turn with no usable figure counts as `unmetered`, and the total is then a lower bound;
- Kilo stores a missing figure as 0, so an all-zero turn is `unmetered`, and a window of nothing else
  is `unavailable`. A zero kind beside real figures counts as missing too: a model that never reports
  cache tokens leaves every total a lower bound until `quota.token_fields` drops those kinds;
- a Kilo cost of 0 means nobody recorded a cost, so USD is `unavailable` until a real cost appears.

An unreadable transcript, or more bytes than `quota.transcript_max_bytes`, makes the reading
`unavailable` rather than a partial sum, because a partial sum would understate what was used. Kilo's
session totals are not used: they hold a session's whole life, not one window.

`claude-snapshot` has nothing to read yet: the command that writes its file comes with the quota surfaces.
Until then that reader reports `unavailable` and `claude-cache` carries the percent.

The snapshot and the sample log are per user (`$XDG_STATE_HOME/ddflow`, or `quota.snapshot_path` and
`quota.samples_path`), because several projects share one account. They hold figures only, with an
account as a short digest, never a raw id. The `[quota]` knobs are in the [configuration table](configuration.md#configuration-1).

## Dependencies on another repository

```toml
[schedule]
repos = ["run_nemo_run=../run_nemo_run"]
```

`ddflow update GEN.4 --needs run_nemo_run:132.D` then waits for item `132.D` **there**
to be done. `ddflow external sync` (and every session-start hook) reads the sibling's log
— never writing it — and records what it observed in this one, only when it changed.
Readiness is decided from that dated fact, so the fold stays pure and "why was this
started?" is answerable later. An unobserved external dependency is unmet; one naming
a repository that is not configured is a `doctor` problem, since it can never be met.

## Status, progress, and loops

```sh
ddflow status      # what is done, in flight, ready, blocked — one answer
ddflow progress    # attempts, hours held, gate runs, commits, per item
ddflow loops       # circular references and runtime loops (exit 2 = none)
```

`progress` counts a landing once (`commits` holds distinct shas). `ddflow show <id>` takes a
**bug** id too: its state, where it was found, the fix task(s) — a task that says "fixes
bug X", or names X in a fix list ("fixes bugs A, B and X"), is a fix, a mention is not — the regression tests it was closed with, and an
invalid closure a later fix superseded. `ddflow --json board` prints the board as JSON (a task under no phase is listed in an **Unphased** section of the board, and under `unphased` in the JSON);
`--agent`, `--repo` and `--json` are accepted after the subcommand as well as before it.

**A phase's done/total is the same number everywhere** — the board, `phase list` (and
`ddflow_read kind=phase`), the progress line after `complete` and the ROADMAP export. A phase counts every live task nested any depth below it, sub-tasks and bug-fix
tasks included; an abandoned task is settled, so it is out of the total and shown as
"N abandoned" where there is room. The progress line after completing a sub-task names
the sub-task's phase, not its parent task.

`next --phase`, `brief --phase` and `board --phase` refuse an id that is not an item
and `next` names the phases that start with it; `board --phase` given a task id refuses,
naming that task's phase. `ddflow status` over MCP is **bounded**: the counts are exact, but each
long list is cut to the 25 most recent (the first 25 in scheduler order for the others),
with a `truncated` note naming the real lengths; the CLI, and `ddflow --json status`,
list everything. The other large MCP reads are bounded the same way, and a cut is never
silent: `ddflow_next` lists the ready items whole and the first 10 blocked ones, with a
`truncated` field giving the exact blocked total and a count per reason; `ddflow_show` leaves
out each gate record's tree ids and merge bookkeeping and cuts any gate or triage string past
160 characters, with `truncated` naming what went; `ddflow_progress` (most effort first) and
`ddflow_decision_list` (newest; no context or alternatives, decision text clipped to 400
characters, `ddflow_decision_show` has one whole) return 25 rows, `limit` raises it and `limit=0`
is all, and a second content block states the cut; `ddflow_recall` drops each hit's raw
record; MCP bodies are
compact JSON. `--json` on the CLI is the whole, indented body in every case. **`recall` enforces `max_chars` (default 4000) once, in the API, for the CLI, `--json` and MCP
alike**: hits are taken one per source in turn so no source is crowded out, counted by the
headline and fenced body a reader is shown, the first hit is always kept whole, a hit that does
not fit is skipped, and a hit whose text repeats one already kept (same words up to case and
whitespace) is folded into it; what the budget or the fold left out is stated (the MCP result's
second block, the CLI's `… truncated at N chars` line). On this repository
that took `next` from 38 KB to 3.9 KB, `show` of a finished task from 8.9 KB to 4.1 KB,
`recall` from 51 KB to 4.1 KB, `decision_list` from 54 KB to 18 KB and `progress` from
158 KB to 8.4 KB. `doctor` and `status` also say when the log holds events from a **newer
ddflow** than this checkout runs — they were skipped, so the numbers are computed without
them — and the remedy is to merge main or run the newer ddflow.

**`brief` says whose work it is.** Run under an identity, its `## Current` is the agent's
own most recent lease (several are listed, with `--item <id>` for another); an agent
holding nothing sees `## Suggested next` — the queue's top ready item, to `claim` first —
because calling it Current told an agent it was working on something it never claimed.
`complete` likewise defaults the author model from the agent's open `session start`.

Dependency cycles are the easy case. The expensive ones are *runtime* loops, where the
graph is perfectly acyclic and the work still never finishes:

| Detector | Catches |
|---|---|
| `dependency_cycle` | A needs B needs C needs A — always blocking |
| `repeat_claims` | claimed and given up N times without completing (crash-expiries excluded: that is a different problem) |
| `gate_flapping` | a gate whose verdict keeps flipping — flaky, or measuring a moving target |
| `repeated_failure` | one gate failing N times in a row (default 3, `max_repeated_failures`) with the same output digest — the same failing patch re-applied, which never flips the verdict so `gate_flapping` misses it; a pass in between resets it, reviewer gates (`rubber_duck`, `critic`) are never counted |
| `reopened` | work that will not stay done, usually because the acceptance criteria are not in the item |
| `duplicate_work` | two live items declaring the same files |
| `no_progress` | N recent events with no completion, no gate pass, no merge |

Every threshold is a `[loops]` knob, and `on_detect = "block"` makes `ddflow claim`
**refuse** an item that is already looping — a warning is read by a human later, a
refused claim is read by the agent now. `repeated_failure` also shows as a line in the
item's `ddflow brief`, and under `block` refuses a `gate run` of that gate until the work
has changed (the refusal names the evidence; a different tree lifts it). `max_repeated_failures = 0`
turns it off.

## The task pipeline

Ten gates, in order, configurable per project:

| # | Gate | Run by | Purpose |
|---|---|---|---|
| 1 | `research` | agent | State a falsifiable claim; probe it before building on it |
| 2 | `rules` | agent | Load project rules + the lessons relevant to *this* task |
| 3 | `implement` | agent | Write the change, in its own worktree |
| 4 | `rubber_duck` | **different-family** model | Try to *refute* the change |
| 5 | `critic` | **different-family** critic | Where does the diff disagree with the intent? |
| 6 | `standards` | tooling | Linters, architecture review, coding-standards MCP |
| 7 | `unit_tests` | tooling | The project's suite, actually executed |
| 8 | `bug_hunt` | agent | Hunt the recurring classes across everything touched |
| 9 | `dedupe` | agent | Did this re-implement something already present? |
| 10 | `merge` | ddflow | Land it, from the primary checkout, with no checkout |

Four things are enforced rather than requested:

**Silence is not a pass.** Every gate in the pipeline must carry *some* outcome before
an item completes — passed, failed, unavailable, partial, or an explicit
`ddflow gate skip <id> <gate> --reason "..."`. Without this, `gates.required` held only
`implement`, `unit_tests` and `merge`, so six of the ten steps could be omitted with no
trace at all. `gates.require_outcome = false` makes the pipeline advisory again;
`gates.enforce_order` ("warn" by default, or "block") reports a gate recorded before an
earlier one has run, because a rubber-duck review recorded before `implement` reviewed
an empty diff.

**UNAVAILABLE is never a pass.** A reviewer whose endpoint was down approved nothing; a
linter that is not installed found nothing. Each gets its own outcome and shows as a
coverage gap. (The inverse matters too: this codebase's first version classified a
*missing binary* — shell exit 127 — as `failed`, so an uninstalled linter looked like a
linter reporting problems. Fixed, with a mutation-verified regression test.)

**Evidence or it did not happen.** Gates in `gates.evidence_required` reject a bare pass;
they want the command, its exit code and its output digest.

**And evidence says WHICH tree and HOW MUCH.** Every gate that produces an OUTCOME —
command gates, and agent gates recorded with `gate record` — carries a
`tree_sha`, ONE identity of the tree it ran against: `<head>+clean` for a tree that holds
exactly HEAD's files, else `<head>+st:<content id>` -- the commit plus the exact content
of every file, tracked changes *and* untracked ones (a new module is untracked until its
first commit, which is the ordinary state of agent work). Older evidence recorded a
digest of the status and diff text beside a separate `source_tree` field; both spellings
are still read, so a pass recorded by an earlier ddflow keeps its meaning.
ddflow's own `.ddflow/` is excluded, or recording a gate's outcome would invalidate the
gate that just recorded it. If the tree moves afterwards, `complete` warns that the pass
describes source nobody is shipping — a warning, not a block, because refusing on a
comment-sized change is how a check gets switched off. The comparison is of **content
trees**, not commit ids, so an amended message is not a change; once the item has
landed it is made against the commit that landed (a merge commit's second parent, which is
what the gates ran on — never the primary checkout, whose files are everyone's), and the
note names what differs. Evidence that cannot be compared says so rather than passing
silently.

Beside it, `diff_stat` records files, insertions, deletions and untracked count —
including the *lines* in untracked files, because a new module is untracked until its
first commit and a task that is entirely new files would otherwise report zero
insertions. `tree_sha` answers *which* tree and is opaque; this answers *how big*,
and that is what makes a pass auditable later — a review gate that passed over 4,000
changed lines in two minutes is a different claim from one that passed over 12.

Neither is recorded for a **skip** (nothing was reviewed, so a magnitude would imply an
inspection that did not happen) nor for an `unavailable` gate that never ran.

Hashing untracked content is capped by `MAX_UNTRACKED_HASHED` (512). Above it the
identity falls back to the older digest, which covers file *names* and says so inside it, because a check
that quietly stopped covering content would go silent for exactly the repositories that
need it most.

**Reviewer independence is checked, and an unidentified reviewer establishes nothing.**
Same-family reviewers share the author's blind spots, so their agreement measures shared
priors rather than correctness. `complete` refuses unless one reviewer came from a
different pretraining family — and a reviewer whose model is not in `[agent].families`
counts as *unknown*, never as *different*. Each `families` key is a case-blind substring of
the model name, as each `routers` key is; an empty key matches nothing. (It used to count as different: `gate record`
defaults the reviewer to the agent id, so a `standards` gate recorded with no `--model`
arrived as family "host-12345", compared unequal to "anthropic", and satisfied the
independence requirement on its own.)

`--model` on `gate record` names the *reviewer*; the author's model is `complete --model`
(or the one declared at `session start`). Recording the author's own model on a reviewer
gate used to overwrite the reviewer's, and `complete` then judged independence against the
author's family. Now a `--model` in the author's family on `rubber_duck`/`critic` is refused
(exit 3, naming `--reviewer-model`); `--reviewer-model <m>` (MCP `reviewer_model`) is the
explicit spelling for a reviewer that really is of that family. The author is the model the
agent declared at `session start --model`; with none declared there is nothing to compare
and the record stands (`complete` says which it used: the `--model` flag or the agent's
session — `author_model`/`model_source` in the result and on the `item.completed` event).
`standards` is covered too, since it counts toward independence.

Which gates are reviewer gates comes from their definitions: `rubber_duck`, `critic` and
`standards`, plus every gate declaring `reviewer = "different_family"` — the built-in
`verify`, and a project's own (`[gate.security_review]` with that line in `gates.toml`).
A review recorded through any of them counts toward independence and has its `--model`
checked as above; a `same_family_ok` gate does neither, built in or not (`critic` set to
`same_family_ok` stops counting). (A custom reviewer gate used to be
ignored, so an item reviewed only through one was refused at `complete`.)

**`--reviewed-sha` ties a review to the commit that merges.** `roborev review HEAD` run from
an item's worktree has enqueued the primary checkout's HEAD, so a `standards` gate was
recorded against the wrong commit. Run `roborev review <sha>` with your branch head and
record `gate record <id> standards --reviewed-sha <sha>`: the full sha is stored in the
evidence, an older commit of the item's branch is accepted with a warning, and any other
commit (main, another branch, an unknown sha, or a symbolic ref such as `HEAD`, which means
a different commit in each checkout) is refused with exit 3. An item with no branch to
compare against records the sha with a note.

**A reviewer tool must leave git as it found it.** `ddflow gate run` on a reviewer gate
(`standards`, or any `different_family` gate with a command) and a `kind = "command"`
reviewer compare HEAD, the index, the working tree and the stash list before and after the
tool runs. The stash list is shared by every worktree, so a tool that ran `git stash apply`
or `pop` (kilo under roborev did) rewrote someone's tree. If anything moved, the gate is
`unavailable` (never passed), the evidence carries `git_state_changed` with both digests,
and nothing is restored: check the tree and `git stash list` yourself. A `roborev review
<sha>` you run by hand is outside this check; run it against a clean throwaway worktree.

**The reviewer recorded is the one roborev ran.** roborev runs its `backup_agent` when the
requested agent fails, so a job enqueued as `agent: kilo` can be reviewed by `claude-code`
(`roborev show N`: "by claude-code"). With `--reviewed-sha` on a reviewer gate, ddflow asks
`roborev list --json` for the newest finished review of that commit (or of a range ending
at it) and records the model roborev ran -- else its agent -- as the reviewer, with the
job under `evidence.roborev`, whatever `--model` said; a same-family reviewer is then
judged as one at `complete`, as `--reviewer-model` would be. Without roborev on PATH, or
with no finished review of the sha, the typed model stands, with a note and
`evidence.roborev.verified = false` in the record.

`ddflow review` of an item reviews the branch's commits plus tracked edits in its worktree.
Untracked files are *not* sent to the reviewer; they are listed in the output and in the
evidence's `diff_source` as "untracked, not reviewed" — commit a file to have it reviewed.

A **router** author is a set of families. Copilot CLI's HydraFusion is chosen like a model
but routes each task across models from several providers, so `[agent].routers` maps its
name to the families it draws on, and a reviewer counts as independent only when its
family is outside the whole set. HydraFusion ships with an empty set — GitHub publishes no
fixed roster — so `complete --model hydrafusion` refuses, naming the knob, until you list
the families your plan routes to:

```toml
[agent]
routers = { hydrafusion = ["anthropic", "openai", "google"] }
```

```console
$ ddflow complete P1.T1 --model claude-opus-5
cannot complete P1.T1 — 1 unmet condition(s):
  - reviewer independence not satisfied: every reviewer (rubber_duck) was family
    'anthropic', the same as the author. Same-family agreement is not independent evidence.
```

Every unmet condition is listed **at once** — a refusal that reveals one problem at a
time trains an agent to reach for `--force`.

---

### Proving a gate can fail at all

```console
$ ddflow gate verify T1 unit_tests
  OK   src/calc.py: detected

unit_tests CAN fail: every registered mutation was caught.
```

A gate that cannot go red is worse than no gate — it reports success on every change,
and everyone downstream reads that as evidence. `gate verify` breaks what the gate
guards, using the `mutations` registered beside it, and requires the gate to notice:

```toml
[gate.unit_tests]
command = "python -m pytest -q"
cwd = "repo"
mutations = [ { file = "src/calc.py", old = "return a + b", new = "return a - b" } ]
```

Four things make it honest rather than ceremonial, and the last one is this feature's
own bug, found by a cross-family review of it:

- **A mutation that did not apply is a FAILURE, not a skip.** If `old` is absent — or
  present twice, so the edit is ambiguous — the check fails. Skipping turns "the
  mutation never happened" into a green run, which reads as the opposite of the truth.
- **The source is restored whatever happens**, including on exception, or a failed
  verification leaves the tree broken and the next gate reports the verifier's fault.
- **A gate with no registered mutations is reported as unproven.** Declaring a check
  nobody has shown can fail is what this exists to catch. An agent gate says plainly
  that it has no command to mutate and rests on its evidence contract instead.
- **A green baseline is required first.** A gate already red for an unrelated reason —
  one pre-existing failing test, a tool that stopped being installed, a flake — reports
  `failed` for every mutation, so every mutation reads as *detected* and the gate is
  certified as able to fail when nothing has shown any such thing. The check written to
  catch the vacuous-pass class contained it. It now runs unmutated first and refuses
  without a pass.

A gate that reads a COMMITTED ref, as `ci` does (`ddflow ci run` merges the branch's
commits in a scratch worktree), cannot see an uncommitted edit, so verifying it that way
reported PASSED on mutated source. Such a gate (`reads_ref = true`, the default for `ci`) is verified against a throwaway commit holding the mutation,
run in a scratch checkout of it; the branch, the index and the working tree do not move.
The mutations are read from the item's own worktree as well as the primary, so one
registered on a branch can be verified before it merges.

### A gate that runs only on the work it concerns

`[gate.<id>] applies_when = ["api/**", "schema/*.sql"]` puts the gate in effect for an
item only when one of those path patterns can cover one of the item's declared globs
(`--globs`). For any other item `ddflow gate status` shows it as `[-] <gate>  -- not
applicable: ...`, naming the patterns and the globs it was judged against; nothing waits
on it, so it is neither `silent` (completion does not demand an outcome), a required gate
that is missing, nor ahead of the gates after it under `gates.enforce_order`. A gate that
already has a recorded outcome always applies, and so does every gate of an item that
declares no globs: a gate is never left out for want of information. It is a gate field,
not a knob -- empty by default, so a project that never sets it sees no change.

A gate listed in `[gates].required` is the exception: the declared globs are agent-editable
(`ddflow update --globs`, no approval), so they never excuse it. It reads `[-] not
applicable` only when the ACTUAL change (the item's diff against its base) touches none of
the patterns, and the reason names the changed paths. With no diff yet, an unreadable one or
an empty one it stays owed. Every other gate is re-judged on the diff at `complete`: a change
that touches its paths brings it back, and `complete` lists each gate the diff excused.

A gate that concerns a branch (`needs_branch`, set on `merge`) has the same shape: it
does not apply to a phase with no branch and no worktree -- a phase has no branch of its
own -- so it shows `[-] merge  -- not applicable: no branch ...` and completes
without a hand-recorded pass. What stands in for it is a fact: `complete <phase>` refuses
while a finished task below it still has a branch the base lacks. An ordinary claimed
task still needs `merge`.

### A pass that must name its evidence

`[gate.<id>] requires_evidence = ["report", "link"]` and `evidence_fields = ["intent",
"edge cases"]` refuse a bare pass: `gate record ... --outcome passed` fails, listing every
unmet form at once, unless the evidence carries what the gate asks for. **report** -- an
output digest (attach the output with `--output-file`; an empty or whitespace-only file
does not count) or a `report_digest`; **link** -- a
record id (a research note) whose item is this one, so a note filed against another item
does not count; **fields** -- every `evidence_fields` name filled in with real content (an
empty value, `n/a`, `none` or `LGTM` counts as missing). Listing `evidence_fields` implies
the fields form. Only a pass is held to it -- a skip with a reason or `unavailable` stays
available -- and, as with `applies_when`, these are gate fields, not knobs: empty by
default, so a project that sets none sees no change. A requirement ddflow cannot check
(a `link` where no record can be resolved) is not met rather than assumed.

### When the exit code is not the verdict

Some tools say "I could not run" or "I only did part of it" with an exit code, and some
exit 0 whatever happened. A command gate can say which:

```toml
[gate.critic]
command = "uv run scripts/critic_review.py --dirty -c configs/review_critic.toml"
unavailable_exits = [2, 143]      # endpoint down / SIGTERM: UNAVAILABLE, not failed
partial_exits = [3]               # reviewed part of the diff: PARTIAL
require_output = '^STATUS:'       # exit 0 without it = the tool did not do its job
fail_output = '^\s*- \[(HIGH|MEDIUM)\]'   # exit 0 WITH findings = failed
```

`ddflow gate run` also renews the caller's lease every `[lease] heartbeat_s` while the
command runs, so a 25-minute suite does not outlive a 30-minute lease and read as
abandoned work.

It keeps the run's **whole output** in `.ddflow/runs/<item>/<gate>-<time>-<pid>.log` (the
newest ten per item and gate; the directory ignores itself, so nothing is committed) and
records its path beside the digest, so the digest can be checked against bytes that still
exist. The evidence also carries the suite's own verdict lines (`summary`: pytest's
`=== 3 failed, 112 passed ===`, unittest's `FAILED (failures=2)`) lifted from the *whole*
output, because a gate that reruns its failures ends on the rerun's "112 passed" and the
tail alone would hide the first pass's "115 failed". `gate record --output-file` keeps the
file's path the same way.

---

## The phase pipeline

```
research → [ task, task, task … ] → unit_tests → bug_hunt → dedupe
         → live_test → corrections → docs → merge
```

`docs` runs once per phase, before it merges: every user-visible change the phase made
(commands, MCP tools, flags, knobs and defaults, output, setup) is checked against the
README and the docs and updated there, with the files changed as its evidence. The
per-commit stale-docs check catches a renamed or removed name; this catches the new
feature nobody wrote down.

`live_test` is the one most often skipped and the one most worth keeping: **a green unit
suite and a working feature are different claims.** Run the real thing on a small input
and paste what it printed.

---

## Human approval: a gate the agent cannot clear

Every other gate here is satisfied by the agent — it runs a command, or it asserts it
did the thinking. That is right for work whose correctness is checkable afterwards, and
wrong for a **plan**: by the time an agent has built the wrong thing, the cost is
already paid.

A gate marked `human = true` is where the operator says *yes, build that* before the
compute is spent.

```toml
# .ddflow/gates.toml
[gate.plan_approved]
title  = "Operator approves the plan"
human  = true
prompt = "Show the operator what you intend to build, then ask."
```

```console
$ ddflow gate run T1 plan_approved
gate 'plan_approved' is a HUMAN-APPROVAL gate. It is not something you can run or
record — it is where the operator decides whether this work should proceed.
  ddflow approve T1 plan_approved
  ddflow approve T1 plan_approved --reject --reason '...'

$ ddflow gate record T1 plan_approved --outcome passed --evidence "looks fine"
'plan_approved' is a human-approval gate: it is cleared by a person, not by an agent
recording that it happened.                                            # exit 3

$ ddflow approve T1 plan_approved --note "read the plan, ship it"
T1.plan_approved approved by delian — read the plan, ship it
```

**A rejection is a first-class outcome**, not the absence of an approval: *"the operator
looked and said no"* and *"nobody has looked yet"* are different states, and an item
sitting in the second forever is how a checkpoint becomes a silent stall. `--reject`
requires `--reason`.

**There is deliberately no MCP tool for this**, and `ddflow/surfaces/exemptions.py`
records the exemption (a field of the command) with that reason. A human checkpoint reachable from the MCP surface is not
a human checkpoint — it is a second `gate record` with a longer name. `gate skip` is
refused too: *"the operator does not need to approve this"* is not the agent's call.

**What this is, precisely.** An audit trail and a speed bump, **not a security
boundary.** An agent with shell access can run `ddflow approve` itself, and no design
here changes that — the tool does not control the machine.

The guarantee, as narrowly as it holds: **no MCP tool records a human outcome**, and a
clearance carries the OS user and a `human` flag, so a forged one is *visible in the
log* rather than indistinguishable from a real one. (`gate.<id>.human` is also refused
by the config writer, because two MCP calls — flip the flag, then record — used to
clear the gate with no shell involved. Declare human gates in `.ddflow/gates.toml`,
which no tool writes.)

Opt-in: the shipped pipeline has no human gate, and a test keeps it that way. When the
`merge` gate itself is declared human, `ddflow merge` still lands the branch but records
no outcome for that gate (`merge_gate_human` is true in its result, and the CLI names the
`ddflow approve <id> merge` to ask for).

**A required gate recorded `partial` because it is baseline-red** (the failing tests also
fail on the base branch, measured by `gate run`) blocks `complete`, and `--force` is not the
way past. Re-run it once the base is green, or have a PERSON run `ddflow approve <id> <gate>`
(decision D-accept-baseline-red-partial): that accepts the one recorded outcome by digest
(`approval.granted`, refused under an agent identity, no MCP tool). The gate stays `partial`
in the record and in `gate status`; `complete` warns that it settled only on that acceptance,
and `verify` notes it. A re-run or re-record needs its own acceptance; `failed`,
`unavailable` and a hand-recorded or `partial_exits` partial cannot be accepted this way.

**A failure the base could not be compared with is `unavailable`**, not new and not
baseline-red: the run on the base gave no verdict for them (pytest aborted with exit 4 or 5,
was cut short with 2 or 3 before reaching them all, or passed having counted no test), so
`baseline.unmeasured` lists the ids and the reason names the cause. A test
the base lacks needs no run and stays a new failure, and one id measured passing there keeps
the gate `failed`. The standing `complete_unavailable` rule does not answer such a gate, since
its tests did fail: re-run it, or have a person decide.

**A test the base also fails, but with different failure text, is a new failure**, not
baseline-red: the comparison uses the failure text, not just the test id, so a branch that adds
a new offender to a test already red on the base `fails` the gate (`baseline.different`, named
in `new_failures`) instead of hiding it behind `still_failing`.

---

### Chores: files that land without a reviewer

Some files are not code: rules, `.mailmap`, `DDFLOW.md`, decision records, generated views. Taking
each through claim, ten gates and a reviewer is ceremony. `ddflow chore commit FILE... -m "message"`
runs the whole arc in one step: it files a task tagged `chore`, claims it, copies the files into
its worktree and commits them, records every skipped gate with its reason, merges and completes (when
every gate of the item's pipeline is skipped or left to the merge: see `skip_gates` below). It
takes no reviewer because two things hold: every file matches `[chore].globs`, and a person approved
that list. A file outside the list is refused before any task exists and takes the normal pipeline.

- `[chore].globs` is the class: the files a chore may touch (the default is the rules, `.mailmap`,
  `DDFLOW.md`, decision records and the generated queue and lessons views).
- `[chore].skip_gates` is the gates a chore skips (by default research, rules, implement,
  rubber_duck, critic, verify, standards, ci, unit_tests, bug_hunt and dedupe). A gate in the
  item's pipeline that is neither listed nor `merge` is left for you to run, and the chore does not
  complete until it carries an outcome.
- `ddflow chore approve [--note "why"]` is the person's approval of exactly THIS list and
  `skip_gates`. It is refused under an agent identity, so an agent cannot widen the class: a
  changed list needs approving again before a chore may use it, whoever edited it. An agent that
  needs the approval gets a request filed and passes the operator's answer as `--approval ID`.

---

## Checking that the checks are working

Three questions ddflow asks about itself, all derived from the log and all reported by
`ddflow doctor`. They exist because **an unmeasured mechanism is indistinguishable from a
missing one**.

**Can the queue's work actually be picked up?** Every other check counts the items that are
present; this one asks whether any of them can be started. An open phase with no task under
it is work `ddflow next` will never offer — a note by default (`schedule.empty_phase`,
since a project that files phases before breaking them down lives there on purpose) — and a
phase whose tasks are *all finished* while the phase stays open is always a problem, because
that is a queue held open by an item nobody can act on.

Tasks cannot go missing here, and that is a property rather than an untested gap: two
hypotheses about how one could were probed and both refuted, and a test now pins the
invariant so a future filter cannot quietly reintroduce it.

**Does a gate ever say yes?** *A gate that fails on everything is worse than no gate: it
trains the next reader to skip it.* A gate at or above `gates.rate_max_fail` once it has
`gates.rate_min_runs` decisive runs is reported as flaky or as measuring a moving target —
re-running it will not converge. A **skipped** gate is not a run, because counting skips as
failures would make an unconfigured gate look like a broken one.

**Did the periodic passes ever fire?** *The mechanism you did not measure is the one that is
not running.* Because a cadence here counts completions rather than wall-clock, this is exact
rather than estimated: `since` is the completions elapsed since the pass last fired, which is
the **same quantity** `ddflow cadence` uses to decide due-ness — deliberately, because two
measures of "is this behind" that can disagree is a situation nobody can reason about. A pass
more than `cadence.max_missed` scheduled runs behind is reported. Being merely *due* is not a
finding (`ddflow cadence` already says that), and running early is not one either.

All three are **notes, not problems**: a defect in the machinery that checks the work must
not block the work.

## Cadences

Periodic whole-repo passes a per-task gate structurally cannot do. Due-ness is **derived
from completed work**, so there is no state file to drift:

```console
$ ddflow cadence
DUE: integration_tests — 5 tasks since last (every 5)
DUE: mutation_tests — 3 phases since last (every 3)

Record one with: ddflow cadence --ran <name>
```

Configurable: integration tests, architecture review, mutation testing, duplication
sweep, lessons compression.

**A phase will not close over an overdue pass.** `ddflow complete <phase>` lists every due
pass counted in phases (architecture review, mutation tests, lessons) as an unmet condition
and refuses (exit 3). Run the pass and record it with `ddflow cadence --ran <name>`, or skip
it on the record with `ddflow cadence --ran <name> --note "skipped: <reason>"`.
Task-counted passes stay advisory, and completing a task never asks. `--force` overrides
(recorded), and every completion path, including a merged pull request, applies the check.
The driver's phase close runs `ddflow cadence` first.

### Scheduled jobs

A cadence pass is one kind of **scheduled job**: a first-class definition of what runs, how
often, on what, and how it may act (decision D-sched-no-daemon). ddflow computes when a job
is due from the log; what starts it is pluggable, and there is no ddflow daemon. A job is
defined in one of three places, and every view shows the merge:

1. the event log: `schedule.defined` (a whole definition), `schedule.updated` (some fields)
   and `schedule.removed` (with a reason; kept, and it hides the same id below it);
2. `.ddflow/schedules/<id>.toml`, one job per file, reviewed in git like any config;
3. `[cadence]`: the five count passes and `every_days` show up as jobs (a pass set to 0 as
   disabled). How they fall due does not change.

An earlier source shadows a later one with the same id, and showing the job names both.

```toml
# .ddflow/schedules/bug-audit.toml
title = "Weekly whole-codebase bug audit"
cadence = { every_days = 7 }        # or { every_tasks = N } / { every_phases = N }
needs = ["integration_tests"]       # due only after these succeeded since its own last run
scope_globs = ["ddflow/**"]         # what a run may write
concurrency_group = "heavy"         # jobs in one group never run at once
prompt = "bug-audit"                # the template a run renders ("" = the job id)
mode = "report"                     # report | fix
budget = { max_items = 5, max_bugs = 10, max_turns = 0 }   # 0 = no cap
escalate = true                     # a run that cannot finish files a needs-operator item
missed = "skip"                     # skip | once: never a catch-up of every missed period
jitter = 30                         # minutes a due run may be spread by
enabled = true
tags = ["audit"]
```

Every definition is validated: an unknown field (a misspelt `scope_glob` would otherwise
widen a job to the whole tree), a wrong type, an id that is not a plain key, a `needs` naming
no job, or a `needs` cycle. A broken file is reported and left out; a recorded definition
that is broken is refused before anything is written. Two enabled jobs may not run at the
same time when they share a concurrency group or their scope globs overlap (shared globs
exempt). A job's runs are the `cadence.ran` records under its id.

The read verbs are `list` (by tag, enabled only), `show` (source, what it shadows, needs and
needed-by, the jobs it may not run beside, runs) and `search` (every word must match). They
are built with their parser (`surfaces/commands/schedule.py`) but not mounted yet: the
`schedule` CLI group and its MCP tool land together with job authoring (add, edit, enable,
disable). Until then they are `api.schedule.schedule_list`, `schedule_show` and
`schedule_search`.

### Event triggers

A **trigger** turns events into work (decisions D-sched-triggers-separate,
D-trigger-actions-create-items). It is defined in `.ddflow/triggers/<id>.toml`, one per
file, reviewed in git like any config -- never an `on:` clause on a rule, so editing an
advisory rule can never arm automation. When its condition is met it **files a queue item**
from a scheduled job's template (the job's title, scope globs, prompt and mode); it never
runs an agent, so the lease, gates and cross-family review apply to the remediation like
any other item.

```toml
# .ddflow/triggers/red-gate.toml
title = "A gate keeps failing on one item"
event = "gate.failed"              # an event kind, or a glob over kinds; never trigger.*
match = { subject = "B-*" }        # optional: subject / agent / kind / a data field -> glob
count = 3                          # N matching events ...
window = 120                       # ... within this many minutes of each other: a sliding
                                   # window over their times (0: any since the key last fired)
key = "{subject}"                  # dedupe key: {subject} {agent} {kind} {data.X}; one open
                                   # remediation per key ("" = one per trigger)
debounce = 10                      # minutes of quiet before it fires
cooldown = 60                      # minutes before the same key may fire again
max_open = 3                       # open remediations of this trigger, at most
hop_limit = 1                      # how deep a chain of remediations may go
breaker = 3                        # consecutive failed or empty remediations that hold it
                                   # (at most 200: the fire history the state keeps)
action = { job = "bug-audit" }     # the scheduled job whose template the item is filed from
                                   # (phase = "P-x" files it under a phase)
enabled = true                     # a new trigger starts DISABLED until the operator says so
tags = ["ci"]                      # added to the items it files
```

`window`, `debounce` and `cooldown` are minutes (whole numbers). Each also has a seconds form,
`window_s`, `debounce_s` and `cooldown_s`, matching every other `_s` knob in ddflow; it takes
a number of seconds or a duration text (`90`, `30m`, `1h30m`; units `s m h d w`), and giving a
knob and its `_s` form together is refused. `window_s = 1800` and `window = 30` are the same
definition.

Every evaluation that finds a condition met is an event: `trigger.fired` (with the items
it filed, the key, the hop and the definition's digest) or `trigger.suppressed` with the
reason -- `disabled`, `debounce`, `cooldown`, `open` (the key's remediation is still open),
`max_open`, `hop_limit`, `breaker`, `item_refused` (the item it would file fails the checks every
new task passes, e.g. `action.phase` names no phase: nothing is filed, the detail says why; a `--dry-run` does not predict it) or
`global_cap` (at most `[triggers].max_fires_per_hour`
fires in any rolling hour across every trigger: default 10, an integer from 0 to 200, the
fire history the log keeps; 0 stops every trigger without disabling one; a bad value in a
config file falls back to 0, the strictest, and `config --set` refuses it) -- and each run
is a `trigger.evaluated`. A trigger never counts `trigger.*` events
or events about the items it filed itself, and a remediation's own failure can re-trigger
only up to `hop_limit`. A remediation that was abandoned or removed, or finished without a
merge, counts against the breaker; after `breaker` of them in a row the trigger is held
until its definition changes. The items carry the tags `trigger:<id>`, `schedule:<job>`,
`mode:<mode>`, `key:<key>` and the trigger's own `tags`. One evaluation reads, decides and
writes under the log lock, so two evaluators started at once cannot both file for a key.

The verbs are `trigger list`, `trigger show <id>` and `trigger evaluate [--now ISO]
[--dry-run]` under the `schedule` group (`api.schedule.trigger_list`, `trigger_show`,
`trigger_evaluate`), mounted with the rest of that group. Nothing runs the evaluator on its
own: like a due job, it is started by whatever the project schedules (D-sched-no-daemon).

---

## Surviving a compaction

The instruction block reaches the model **once**, at connect. After a context compaction it
may retain none of it, and MCP has no server-to-client primitive for injecting context —
the three that exist (`roots/list`, `sampling/createMessage`, `elicitation/create`) all go
the other way or ask a question. Three things already survive:

- the **AGENTS.md / CLAUDE.md sections** `ddflow adopt` (or `ddflow_setup`) writes, plus each agent's native
  rules file — the client re-reads its own rules, so this is the durable channel;
- the **commit hook**, which refuses a commit with no item trailer and says what to add.
  Enforcement at the moment of the act needs no context at all;
- `ddflow help <topic>`, which the agent can ask for — if it thinks to.

What none of those do is **speak up unprompted**. A footer on tool results is the only
channel that is guaranteed to be heard again, because an agent driving ddflow calls tools
continuously:

```
ddflow: left undone in this project —
  · 1 bug(s) still open: B1 — close with `ddflow_bug_fixed` (it requires the regression test) or say why not
  · 2 gate(s) skipped, not run: T4.critic, T4.standards — run them, or leave the skip on the record deliberately
```

**It is not a banner, and the difference is the whole design.** A fixed reminder appended to
63 tools is trained out inside a session and costs tokens on every call. This one:

- **names what happened**, never restates a rule — an id, a count, and the call that
  discharges it;
- **stops** once the thing is dealt with, so it cannot be trained out by repetition;
- **says nothing at all** when the project has nothing outstanding — not a cheerful "all
  clear", which is the same thing readers learn to skip;
- is **cadenced**: at most once every `every_calls` calls *and* `every_seconds` seconds, so
  a burst of calls is not a burst of footers;
- **cannot break the call it rides on**. It is a courtesy on top of an answer, appended
  after the body, and a failure inside it is swallowed. `content[0]` is still the
  structured result.

```toml
[reinstruct]
enabled      = true   # false silences it entirely
every_calls  = 12
every_seconds = 240
max_items    = 3
```

What it currently notices: bugs found and never closed, gates skipped and never revisited,
and work finishing with no lesson ever recorded (after `[lessons] reflect_after_items`, so
one task is not reported — the pattern is, and the threshold is a knob because where the
line sits is a judgement).

---

## Keeping session-start cost flat

```sh
ddflow brief --phase P2
```

Returns, inside `session.brief_max_tokens` (default 1200): recoverable work first, then
the current item and its remaining gates, then what is ready, then why everything else is
blocked, then the handful of past lessons **ranked against this task's text**.

This *replaces* reading the project's rule and lesson corpora. When the whole does not
fit, each section is cut to its share of the budget and ends with one line saying how much
it left out and where the rest is (`ddflow recover`, `ddflow job list`, `ddflow next`,
`ddflow decision applicable <id>`, `ddflow memory list`, `ddflow recall <topic>`); the
decisions and lessons left out are named by id (the first six, then a count), and each
decision is quoted up to 280
characters (`ddflow decision show <id>` for the whole). What a short section does not use
goes to the sections that need more, so the safety-critical head survives a squeeze and
no long section ahead of them — other items' jobs, leftovers, blocked items — can cut the
decisions, rules and lessons out. Long-running jobs: those running here, the item's own
and the ones the brief's own agent started are listed in full (the item's first); other
agents' exited or killed jobs collapse to one count line pointing at `ddflow job list`. A
project's opening cost stays roughly constant as its lesson corpus grows.

---

## What is automated, and what is not

The honest split, because a tool that claims to automate judgement is lying about the
part that matters.

**Automated — happens without anyone remembering it:**

* **The handshake briefs the agent.** On connect, the MCP server injects the live state:
  the pipeline every task must pass, work recoverable after a crash, what is ready, which
  companions are missing, and what to do about each. It is a template
  (`ddflow prompts eject mcp_instructions`), so the workflow is text you edit, not code
  you fork.
* **Gates are enforced, not suggested.** `ddflow complete` refuses on a required gate
  that has not passed, on open sub-tasks, on a silent gate under `require_outcome`, on a
  requirement no pipeline runs, and on a reviewer from the author's own family. Refusals
  list *every* unmet condition, not the first — an agent that cannot see how many more
  are coming reaches for `--force`.
* **Conflicts are refused at claim time**, by glob overlap, with an alternative named.
* **Dependencies gate readiness.** `ddflow next` withholds a task whose `needs` are open
  and says which.
* **Bugs are offered before features.** A task tagged `bug`/`fix`/`hotfix` (the
  `[flow]` bugfix and hotfix tags), or named by an open bug record, comes ahead of every
  feature in `ddflow next` and gets a free slot first, so a standing bug is fixed before
  more work is built on it. Priority orders each group; `[schedule] bugs_first = false`
  orders by priority alone.
* **Bugs are queue items.** `bug found` files the fix task `fix-<bug>` by default -- in
  the standing `bugs` phase, on the named item's files -- so a feature touching the
  same files waits behind it, and the bug closes when that task completes with a
  regression test (`complete fix-<bug> --regression-test ...`). See [Bugs are queue
  items](knowledge.md#bugs-are-queue-items).
* **Gate evidence records which tree and how much** — one tree identity (commit plus content) plus
  files/lines changed — so a pass names what it passed on. If the tree moves afterwards,
  `complete` warns that the evidence describes source nobody is shipping.
* **Crash recovery**: `ddflow recover` finds worktrees whose lease expired, so an
  interrupted agent's work is found rather than lost.
* **Cadences** (`ddflow cadence`) tell you which periodic passes are due — bug hunts,
  dedupe, lesson compression — from the log, counted in completed work; and, for a rule
  like "a bug hunt every week", by the calendar (`[cadence] every_days =
  ["bug_hunt=7"]`: never run means due now). The SessionStart hook lists what is due.
  A phase will not complete while a phase-counted pass is overdue.
* **Post-merge review**: `ddflow review <item> --commit <sha>` reviews one landed
  commit against its first parent — a merge as what it brought in — when the branch
  is gone.
* **A commit hook** (`ddflow hooks install`) can refuse an unclaimed edit outright,
  and refuses a staged `ddflow render` view that the log no longer regenerates
  byte-for-byte — hand-edited, or stale (`[enforce] generated_views`). It also reports
  a doc line still naming an identifier, file or default the commit removes or renames
  (`[enforce] stale_docs`, `doc_globs`, `doc_exclude`; warns by default). With `[flow].environments` set, a commit made directly on an environment branch is refused (`[enforce] environment_commits` = block | warn | off): work on a branch and promote it (`ddflow promote status`); merge and squash commits, which is how a promotion lands, pass. It refuses a
  commit on a branch whose base changed a rulebook (AGENTS.md, CLAUDE.md, each agent's
  native rules file, the driver docs) since it forked (`[enforce] stale_rules`), and warns
  past `max_behind` commits behind (`[enforce] behind`); the commit concluding
  `git merge <base>` is exempt, and the session hook only informs. Its commit-msg
  sibling requires an `Item:` trailer when `[enforce] require_item_trailer` is on — or
  the project's own keys (`item_trailer_keys = ["Phase", "Phase-ships"]`); merges are
  exempt. The value must be the id of an item in the queue (any state but removed): a
  mistyped id is refused with the nearest real ones, and a queue the hook cannot read is
  exit 2, never a pass. A key that marks a commit shipping no item takes only its declared
  words instead (`trailer_waivers = { "Phase-ships" = ["none", "recon"] }`). It also refuses any trailer named in `[enforce] forbidden_trailers` (e.g. a
  tool-attribution line), merges included, for every agent and every route that runs
  git hooks -- which a harness-side hook reading only the command text cannot promise.
  The hook's refusals say what to do. A **lapsed lease** nobody has taken over, on the item
  whose tree you commit from (or held by you), is accepted with a note naming when it lapsed
  and the heartbeat that renews it (`ddflow --agent <holder> heartbeat <id>`);
  `[enforce].lapsed_lease` = `holder` accepts only the holder's own name, `refuse` restores
  refusing it with that remedy (and, for anyone else, `recover --item`, `release --note
  salvaged`, `claim`). Paths ANOTHER agent's live lease covers are never accepted: each
  lapsed lease is judged path by path, so one whose globs overlap a live lease still
  vouches for the staged paths nobody holds (a merge of main stays committable), while the
  held paths keep the STOP. When
  nothing declared who is committing, it says the name was derived from the tree and how to
  declare one. A **clean merge commit** — the automatic one concluding `git merge <base>`,
  or a squash that names what it squashes — passes without a lease lookup, and a conflicted
  merge is judged only on the paths the merge changed, not on everything the base brought
  in. `ddflow merge` itself aborts a merge it fails, and `doctor` flags a primary checkout
  left mid-merge with the command that clears it.
  Over a hook the **pre-commit framework** generated, `hooks install` does not edit it (the
  next `pre-commit install` would discard the edit): it advises a `repo: local` hook in
  `.pre-commit-config.yaml` (`ddflow precommit` proposes it), says nothing needs installing
  when the framework already runs ddflow's check, and `--force` replaces the generated hook.
* **Hooks say which ddflow wrote them.** A git hook, a Claude/Gemini hook command and the
  frozen-files block `onboard` adds to `.pre-commit-config.yaml` are each a stamped
  region (`# ddflow:begin hooks/pre-commit ddflow=<version> fmt=<level> sha=<digest>` ...
  `# ddflow:end ...`; the Claude/Gemini command carries its markers as shell comments).
  `hooks install` refreshes the region and keeps every line around it; an older ddflow
  refuses (`upgrade ddflow to >= X`) to rewrite one a newer format level wrote, and a region
  you edited by hand is copied to `.ddflow/backups/` first. A git hook written before the stamp
  (it carries only the `# DDFLOW-HOOK v1` line, which the region keeps for older ddflow
  versions) is still ddflow's and, as it always was, is replaced whole (lines you added to
  one are not kept; add them after upgrading). `hooks uninstall` removes the
  region and leaves your own lines.
* **Hooks find ddflow when they run, and fail open.** The git hooks and the Claude/Gemini
  hook commands record the launcher that installed them, but try it only first: if that
  script or interpreter no longer exists or is no longer executable (a deleted venv, an uninstalled tool, a removed
  worktree) they run `ddflow` from `PATH`, and when there is none they print one line
  (`ddflow: not found ... run: ddflow hooks install`) and let the commit or turn go on.
  A missing tool never blocks a commit, but a check that silently stopped is reported:
  `ddflow hooks status` prints a `DANGLING LAUNCHER` line and `ddflow doctor` a problem
  (a note when `ddflow` on `PATH` still runs the hook) for each git hook, Claude/Gemini
  hook command and `ddflow` MCP entry (`.mcp.json` and the other agents' JSON configs) whose
  recorded path is gone, naming the refresh: `ddflow hooks install` (git hooks),
  `ddflow hooks install --claude` / `--gemini` (harness hooks), `ddflow adopt` (MCP entries).
  On a **fresh clone** of an adopted project `doctor` also prints a note that the managed
  git hooks are not installed (git does not track hooks; run `ddflow hooks install`), a
  problem only under `[enforce].commit_without_lease = "block"`, and a note when a tracked
  agent config (`.mcp.json`, `.claude/settings.json`, ...) records an absolute interpreter
  or `PYTHONPATH` of one machine: on any other it dangles. Use `ddflow adopt --launch uvx`
  or `ddflow` on `PATH`, or git-ignore them (bug Bec9a971c5d).
* **A per-task README check** (decision D-readme-current). A task whose diff changed
  code under `[enforce] readme_code_globs` (default `ddflow/**`) but none of
  `readme_files` (default `README.md`), with no `docs` outcome recorded for it, is
  reported — `README not updated: record the section you changed, or gate skip docs
  --reason ...` — by `ddflow complete`, in `ddflow gate status`, and as a `docs:` line
  under the item in `ddflow brief`. Record the reason with
  `ddflow gate skip <id> docs --reason "..."` (or `gate record <id> docs --outcome
  passed --evidence "<section>"`). `[enforce] readme_with_code` picks the severity:
  `warn` (default), `block` (refuses `complete`) or `off`. Test files (`tests/`,
  `test_*`, `*_test.*`, `*.test.*`, `*_spec.*`), documentation (`docs/`,
  `.md`/`.rst`/`.adoc`/`.txt`) and ddflow's own event-log commits (`.ddflow/**`) are never
  reported; when git cannot say what the task changed, `complete` says the check could not
  run (a warning, never a block). It is a check, not a pipeline gate, so a task with
  nothing to document is not asked for a `docs` outcome; the phase-level `docs` gate is
  unchanged.
* **A Claude Code SessionStart hook** (`ddflow hooks install --claude`) puts the brief —
  crashed work to recover, ready items, binding decisions, operational memory — into
  every session, including after a context compaction, whether or not the agent
  remembers to ask. In a worktree behind its base branch it says so, and names the
  rulebooks that changed there. It is added beside the project's own hooks in
  `.claude/settings.json`, removed alone, and always exits 0.
* **A prompt-capture hook** (also installed by `ddflow hooks install --claude`, and by
  `ddflow adopt` for Claude Code and Gemini CLI) records every operator prompt without the
  agent having to remember `session prompt`. Claude Code's `UserPromptSubmit` hook runs
  `ddflow hooks prompt` with the hook JSON on stdin; it opens (once) and reuses a session
  keyed on the harness session id, redacts secrets before anything touches disk, appends
  one `session.prompt`, prints nothing, never waits on a silent stdin and always exits 0,
  so a failing ddflow never breaks the turn. An agent that also calls `session prompt`
  with the same words within two minutes is not recorded twice, and a hook that fires
  twice records once. Gemini CLI is wired the same way through its `BeforeAgent` hook
  (`ddflow hooks install --gemini`, `.gemini/settings.json`). Cursor (`beforeSubmitPrompt`
  in `.cursor/hooks.json`), Codex, Kilo and opencode have no wiring yet: known gaps.
  `ddflow hooks status` reports whether the prompt hook is installed, and
  `ddflow hooks uninstall --claude` (or `--gemini`) removes only ddflow's own hooks.
* **One hook entry point for every agent**: `ddflow hooks run <event> --harness <id>`,
  where `<event>` is one of the canonical lifecycle events (`session_start`, `prompt`,
  `pre_tool`, `post_tool`, `pre_compact`, `stop`, `session_end`) and `<id>` is an agent's
  descriptor in `ddflow/harnesses/`. The agent's JSON goes in on stdin and is read in that
  agent's own dialect (Claude's `session_id`, Copilot's `sessionId`, Cursor's
  `conversation_id`, Cline's `taskId`...); the reply is shaped the way that agent's hook
  contract accepts it (plain stdout for Claude Code and Codex, `additionalContext` JSON for
  Gemini CLI and VS Code, `{"additionalContext"}` for Copilot, `additional_context` for
  Cursor, `contextModification` for Cline) and context is printed only at the events the
  descriptor says the agent honours. It always exits 0 and never raises: a failing hook can
  block the very turn it observes, so a problem is a one-line note on stderr. The older
  `ddflow hooks session-start`, `prompt` and `pre-compact` are aliases of
  `hooks run ... --harness claude` (`prompt --gemini` of `--harness gemini`), and
  `ddflow hooks install|uninstall --harness claude|gemini` is `--claude` / `--gemini`; the
  other agents' hook files arrive with their writers, so `--harness cursor` fails with
  that said. `pre_tool`, `post_tool`, `stop` and `session_end` are accepted and do nothing yet.
* **Every prompt and note carries a session id, even an implicit one.** The id on
  `ddflow session prompt|note` (and the MCP `ddflow_session_prompt|note`) is optional.
  Without one, the words go to the session named by `DDFLOW_SESSION_ID`/`CLAUDE_SESSION_ID`
  if that session is open, else this agent's most recently active open session, else a
  new session marked `implicit` in its start event. The output says which was used
  (`recorded (0 redaction(s)) in session S (latest)`, `... (implicit, new)`); an explicit
  id is unchanged and prints nothing extra. An ended session is never reused, text is
  never refused for a missing id (only empty text is), and redaction runs before disk
  as ever. The prompt hook does the same when a harness gives it no session id. Events
  recorded earlier with no session id show up as a `ddflow doctor` note;
  `ddflow session adopt-orphans` attaches each to the session nearest in time (writing a
  copy marked `adopted_from`, since the log is append-only; run twice it adds nothing, and
  `replay` shows the words once). With `session.log_prompts = false` a prompt is not
  recorded and the output says so, rather than opening a session for nothing.
* **Sessions can be read back.** `ddflow session list` prints sessions newest activity
  first with the agent, span, prompt/note/item counts, `open` or `ended` and an `implicit`
  marker (`--state open|ended`, `--owner` (the session's agent; `--agent` is who you are), `--since <date|timestamp>`, `--limit`, `--json` prints the same
  `{rows, total, shown, truncated, filters}` object as the other `list` commands;
  exit 2 when nothing matches, 3 for a bad filter). `ddflow session show <id>` prints one
  session: every prompt and note in order with its item, then the end summary, redacted
  like the exports; an unknown id is refused with the near matches. Both read the log
  directly, so they work with `export sessions` disabled; an adopted orphan appears once,
  and a prompt nobody has adopted yet belongs to no session (`doctor` counts those). No MCP
  tool yet (planned: `ddflow_list kind=session`).

**Not automated, on purpose:**

* **Installing anything.** Detection is read-only; the report is advice.
* **The judgement inside an agent gate.** ddflow records that you claim to have hunted
  bugs; it cannot check that you did. What it *can* do — and does — is make silence
  visible: a gate never run and never skipped blocks completion, so the failure mode is
  a refusal rather than a quiet omission.
* **Deciding whether a plan is right.** That is what a `human = true` gate is for.
* **Pushing, releasing, or anything outward-facing.**

The design assumption is that an agent's *honesty* cannot be verified, so the system is
built to make an unverifiable claim expensive to make and easy to see: evidence
contracts, mutation-verified gates, coverage gaps recorded on completion, and an exit
code that distinguishes "could not" from "did not need to".

---
